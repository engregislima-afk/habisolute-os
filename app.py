# -*- coding: utf-8 -*-
# Habisolute — Sistema de OS (Streamlit)
# versão com: PDF assinado + cadastro de obras completo + exportar excel + sidebar laranja

import io, re, os, json, base64, tempfile, zipfile, hashlib, hmac, secrets, calendar
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

import streamlit as st
import pandas as pd

# tentar ter requests para buscar CNPJ
try:
    import requests
except Exception:
    requests = None

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
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
)
try:
    from reportlab.platypus import KeepTogether
except Exception:
    from reportlab.platypus.flowables import KeepTogether

# =============================================================================
# Identidade / Config
# =============================================================================
SYSTEM_NAME = "Habisolute — Sistema de OS"
SYSTEM_CODE = "hab_os"

BRAND_COLOR = "#f97316"  # laranja

st.set_page_config(page_title=SYSTEM_NAME, layout="wide")

BASE_DIR   = Path(__file__).resolve().parent
PREFS_DIR  = BASE_DIR / f".{SYSTEM_CODE}"; PREFS_DIR.mkdir(parents=True, exist_ok=True)
USERS_DB   = PREFS_DIR / "users.json"
AUDIT_LOG  = PREFS_DIR / "audit.jsonl"
PREFS_PATH = PREFS_DIR / "prefs.json"
SIGNATURE_PATH = PREFS_DIR / "signature.png"

# =============================================================================
# Preferências simples
# =============================================================================
def _save_all_prefs(data: Dict[str, Any]) -> None:
    tmp = PREFS_DIR / "prefs.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PREFS_PATH)

def _load_all_prefs() -> Dict[str, Any]:
    try:
        if PREFS_PATH.exists():
            return json.loads(PREFS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}

def load_user_prefs(key: str = "default") -> Dict[str, Any]:
    return _load_all_prefs().get(key, {})

def save_user_prefs(prefs: Dict[str, Any], key: str = "default") -> None:
    data = _load_all_prefs()
    data[key] = prefs
    _save_all_prefs(data)

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
            "level": level,
            "action": action,
            "meta": meta or {},
            "system": SYSTEM_CODE,
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
            if not line:
                continue
            try:
                rec = json.loads(line)
                rows.append({
                    "ts": rec.get("ts"),
                    "user": rec.get("user"),
                    "level": rec.get("level"),
                    "action": rec.get("action"),
                    "meta": json.dumps(rec.get("meta") or {}, ensure_ascii=False),
                    "system": rec.get("system", ""),
                })
            except Exception:
                continue
    df = pd.DataFrame(rows, columns=["ts","user","level","action","meta","system"])
    if not df.empty:
        df = df.sort_values("ts", ascending=False).reset_index(drop=True)
    return df

# =============================================================================
# Estado
# =============================================================================
s = st.session_state
s.setdefault("logged_in", False)
s.setdefault("username", None)
s.setdefault("is_admin", False)
s.setdefault("role", "usuario")
s.setdefault("must_change", False)
s.setdefault("theme_mode", load_user_prefs().get("theme_mode", "Claro"))
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
# Auth simples (JSON)
# =============================================================================
def _hash_password_simple(pw: str) -> str:
    return hashlib.sha256((f"{SYSTEM_CODE}|" + pw).encode("utf-8")).hexdigest()

def _save_users(data: Dict[str, Any]) -> None:
    tmp = USERS_DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(USERS_DB)

def _bootstrap_admin(db: Dict[str, Any]) -> Dict[str, Any]:
    db.setdefault("users", {})
    if "admin" not in db["users"]:
        db["users"]["admin"] = {
            "password": _hash_password_simple("1234"),
            "is_admin": True,
            "active": True,
            "must_change": True,
            "role": "admin",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    return db

def _load_users() -> Dict[str, Any]:
    try:
        if USERS_DB.exists():
            raw = USERS_DB.read_text(encoding="utf-8").strip()
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict) and "users" in data:
                    fixed = _bootstrap_admin(data)
                    if fixed is not data:
                        _save_users(fixed)
                    return fixed
    except Exception:
        pass
    default = _bootstrap_admin({"users": {}})
    _save_users(default)
    return default

def user_get(username: str) -> Optional[Dict[str, Any]]:
    return _load_users().get("users", {}).get(username)

def user_set(username: str, record: Dict[str, Any]) -> None:
    db = _load_users()
    db.setdefault("users", {})[username] = record
    _save_users(db)

# =============================================================================
# CSS — degradê laranja no sidebar
# =============================================================================
def _inject_css(theme: str | None = None):
    mode = (theme or st.session_state.get("theme_mode") or "Claro").strip().lower()
    if mode == "claro":
        HB_BG, HB_CARD, HB_BORDER, HB_TEXT, HB_MUTED, HB_GLASS = "#f3f4f6", "#ffffff", "#e2e8f0", "#0f172a", "#475569", "rgba(15,23,42,.02)"
    else:
        HB_BG, HB_CARD, HB_BORDER, HB_TEXT, HB_MUTED, HB_GLASS = "#0f1116", "#141821", "#2a3142", "#f8fafc", "#94a3b8", "rgba(255,255,255,.03)"

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
  background: var(--hb-bg)!important;
  color: var(--hb-text)!important;
}}
[data-testid="stSidebar"] {{
  background: radial-gradient(circle at top, rgba(249,115,22,.5) 0%, rgba(249,115,22,0) 50%), linear-gradient(180deg, #2f3137 0%, #d1d5db 100%) !important;
  border-right: 1px solid rgba(148,163,184,.25);
  backdrop-filter: blur(10px);
}}
[data-testid="stSidebar"] .sidebar-content, [data-testid="stSidebar"] * {{
  color: #fff !important;
}}
.hb-side-title {{
  display:flex; gap:.5rem; align-items:center; margin:.25rem 0 1rem 0; font-weight:800;
}}
.hb-dot {{
  width:10px; height:10px; border-radius:999px;
  background: linear-gradient(90deg, var(--hb-accent), var(--hb-accent2));
  box-shadow:0 0 10px rgba(249,115,22,.55);
}}
[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label {{
  position:relative; display:flex; align-items:center; gap:.6rem; padding:.55rem .75rem;
  border-radius:14px; border:1px solid transparent; background: rgba(255,255,255,.04);
  transition:all .15s ease; margin:.15rem 0; cursor:pointer;
}}
[data-testid="stSidebar"] .stRadio input[type="radio"]{{opacity:0; position:absolute; left:-9999px;}}
[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label:hover{{
  background: rgba(255,255,255,.12); border-color: rgba(255,255,255,.10);
}}
[data-testid="stSidebar"] .stRadio input[type="radio"]:checked + div{{
  color:#0f172a!important;
  background: radial-gradient(circle at top, #fff 0%, #ffe0c7 90%);
  box-shadow:0 6px 26px rgba(249,115,22,.35);
  font-weight:800;
  border-radius:14px;
}}
.card{{
  background: rgba(255,255,255,.95);
  border:1px solid rgba(148,163,184,.40);
  border-radius:18px;
  padding:16px;
  margin-bottom:14px;
  box-shadow:0 6px 30px rgba(15,23,42,.08);
}}
.section-title{{
  background: linear-gradient(90deg, var(--hb-accent), var(--hb-accent2));
  color:#111; font-weight:800; text-align:center;
  padding:.6rem .8rem; border-radius:12px; margin:0 0 12px 0;
}}
.stTextInput input, .stTextArea textarea, .stNumberInput input, .stDateInput input {{
  background:rgba(248,250,252,.7)!important;
  border:1px solid rgba(148,163,184,.45)!important;
  border-radius:12px!important;
  color:var(--hb-text)!important;
}}
.stButton>button, .stDownloadButton>button {{
  background: linear-gradient(180deg, var(--hb-accent), var(--hb-accent2));
  color:#111!important; font-weight:800; border:0; border-radius:12px; padding:.55rem 1rem;
}}
.hb-topbar {{
  height:6px; background: linear-gradient(90deg, var(--hb-accent), var(--hb-accent2));
  border-radius:6px; margin:4px 0 10px 0;
}}
</style>
""", unsafe_allow_html=True)

_inject_css()

# =============================================================================
# Flash / banner
# =============================================================================
def banner(kind: str, text: str):
    icon = {"success":"✅", "error":"⛔", "warn":"⚠️", "info":"ℹ️"}.get(kind, "ℹ️")
    st.markdown(
        f"""<div class="hb-banner {kind}" style="display:flex;gap:.5rem;align-items:flex-start;background:rgba(248,250,252,.9);border-left:5px solid #f97316;border-radius:14px;padding:.35rem .6rem;margin:.35rem 0;">
                <div>{icon}</div>
                <div style="flex:1;">{text}</div>
            </div>""",
        unsafe_allow_html=True,
    )

def flash(kind: str, text: str):
    q = st.session_state.get("_flash", [])
    q.append({"k": kind, "t": text})
    st.session_state["_flash"] = q

def flash_render(clear: bool = True):
    q = st.session_state.get("_flash") or []
    for m in q:
        banner(m["k"], m["t"])
    if clear:
        st.session_state["_flash"] = []

def _render_header():
    st.markdown("<div class='hb-topbar'></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='card' style='padding:.8rem 1rem;'><b>🏗️ {SYSTEM_NAME}</b></div>", unsafe_allow_html=True)

# =============================================================================
# Assinatura digital helpers
# =============================================================================
def save_signature_file(uploaded_file) -> bool:
    if uploaded_file is None:
        return False
    SIGNATURE_PATH.write_bytes(uploaded_file.getvalue())
    return True

def load_signature_bytes() -> bytes | None:
    if SIGNATURE_PATH.exists():
        return SIGNATURE_PATH.read_bytes()
    return None

# =============================================================================
# Login UI
# =============================================================================
def _recover_admin():
    db = _load_users()
    db = _bootstrap_admin(db)
    _save_users(db)
    flash("success", "Admin resetado para admin / 1234.")

def _auth_login_ui():
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.markdown("<div class='section-title'>🔐 Entrar</div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1.3, 1.3, 0.7])
    with c1:
        user = st.text_input("Usuário", key="login_user", placeholder="Usuário")
    with c2:
        pwd = st.text_input("Senha", key="login_pass", type="password", placeholder="Senha")
    with c3:
        st.write("")
        if st.button("Acessar", use_container_width=True):
            rec = user_get((user or "").strip())
            if not rec or not rec.get("active", True):
                flash("error", "Usuário inexistente ou inativo.")
            elif _hash_password_simple(pwd) != rec.get("password"):
                flash("error", "Senha incorreta.")
            else:
                s["logged_in"] = True
                s["username"] = (user or "").strip()
                s["is_admin"] = bool(rec.get("is_admin", False))
                s["role"] = rec.get("role", "usuario")
                s["must_change"] = bool(rec.get("must_change", False))
                prefs = load_user_prefs()
                prefs["last_user"] = s["username"]
                save_user_prefs(prefs)
                flash("success", f"Bem-vindo, {s['username']}!")
                _rerun()
    st.markdown("</div>", unsafe_allow_html=True)

def _force_change_password_ui(username: str):
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.markdown("<div class='section-title'>🔑 Definir nova senha</div>", unsafe_allow_html=True)
    p1 = st.text_input("Nova senha", type="password")
    p2 = st.text_input("Confirmar", type="password")
    if st.button("Salvar senha", use_container_width=True):
        if len(p1) < 4:
            banner("warn", "Use ao menos 4 caracteres.")
        elif p1 != p2:
            banner("error", "As senhas não conferem.")
        else:
            rec = user_get(username) or {}
            rec["password"] = _hash_password_simple(p1)
            rec["must_change"] = False
            user_set(username, rec)
            flash("success", "Senha alterada!")
            s["must_change"] = False
            _rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# gate
if not s["logged_in"]:
    _auth_login_ui()
    flash_render()
    st.stop()

if s.get("must_change"):
    _force_change_password_ui(s["username"])
    flash_render()
    st.stop()

# header
_render_header()
nome_login = s.get("username") or load_user_prefs().get("last_user") or "—"
st.markdown(
    f"<div class='card'>👋 Olá, <b>{nome_login}</b> — <span style='opacity:.9'>Usuário</span></div>",
    unsafe_allow_html=True
)

# toolbar
col_t1, col_t2, col_t3 = st.columns([1, 1, 1])
with col_t1:
    s["theme_mode"] = st.radio(
        "Tema", ["Claro", "Escuro"],
        horizontal=True,
        index=0 if s.get("theme_mode") == "Claro" else 1,
        key="theme_sel_main"
    )
with col_t3:
    st.write("")
    if st.button("Sair", use_container_width=True):
        s["logged_in"] = False
        flash("info", "Sessão encerrada.")
        _rerun()

# persistir tema
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
    anexo_cnpj = Column(String)
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

DB_PATH = BASE_DIR / "os_habisolute.db"
engine = create_engine(f"sqlite:///{DB_PATH}", future=True, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base.metadata.create_all(engine)

def _create_index_safe(conn, stmt: str):
    try:
        conn.exec_driver_sql(stmt)
    except Exception:
        pass

with engine.begin() as conn:
    _create_index_safe(conn, "PRAGMA journal_mode=WAL;")
    _create_index_safe(conn, "PRAGMA synchronous=NORMAL;")
    _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS ix_os_obra_data ON os(obra_id, data_emissao);")
    _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS ix_os_status ON os(status);")
    _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS ix_os_numero ON os(numero);")
    _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS ix_ositem_osid ON os_itens(os_id);")
    _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS ix_medicoes_obra ON medicoes(obra_id);")

# garantir colunas antigas
def _ensure_obras_extra(engine):
    with engine.begin() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info('obras')").fetchall()}
        if "anexo_proposta" not in cols:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN anexo_proposta TEXT")
        if "anexo_contrato" not in cols:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN anexo_contrato TEXT")
        if "anexo_cnpj" not in cols:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN anexo_cnpj TEXT")
_ensure_obras_extra(engine)

# =============================================================================
# Helpers
# =============================================================================
STATUS_OPTIONS = ["Aberta", "Em Execução", "Medido em Aberto", "Medido", "Concluída", "Cancelada"]

def to_df(sess: Session, table) -> pd.DataFrame:
    rows = sess.execute(select(table)).scalars().all()
    if not rows:
        return pd.DataFrame()
    recs = [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows]
    return pd.DataFrame(recs)

def gerar_numero_os(sess: Session) -> str:
    ano = datetime.now().year
    prefix = f"HAB-{ano}-"
    ultimo = (
        sess.execute(
            select(OS).where(OS.numero.like(f"{prefix}%")).order_by(OS.id.desc())
        )
        .scalars()
        .first()
    )
    seq = 1
    if ultimo:
        try:
            seq = int(str(ultimo.numero).split("-")[-1]) + 1
        except Exception:
            seq = (ultimo.id or 0) + 1
    return f"{prefix}{seq:04d}"

def format_brl(v: float) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"

BACKUPS_DIR = BASE_DIR / "backups"; BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
def make_full_backup() -> Path:
    db_path = DB_PATH
    anexos_root = BASE_DIR / "anexos"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = BACKUPS_DIR / f"backup_{ts}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if db_path.exists():
            zf.write(db_path, arcname=f"database/{db_path.name}")
        if anexos_root.exists():
            for p in anexos_root.rglob("*"):
                if p.is_file():
                    zf.write(p, arcname=str(p.relative_to(BASE_DIR)))
    return zip_path

ANEXOS_DIR = BASE_DIR / "anexos" / "obras"; ANEXOS_DIR.mkdir(parents=True, exist_ok=True)
_VALID_KINDS = {"cnpj", "proposta", "contrato"}

def _save_anexo(uploaded_file, obra_id: int, kind: str) -> str | None:
    if uploaded_file is None:
        return None
    kind = (kind or "").lower().strip()
    if kind not in _VALID_KINDS:
        raise ValueError(f"Tipo de anexo inválido: {kind}")
    ext = Path(uploaded_file.name or f"{kind}.bin").suffix.lower() or ".bin"
    obra_dir = ANEXOS_DIR / f"obra_{int(obra_id)}"; obra_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = obra_dir / f"{kind}_tmp{ext}"
    tmp_path.write_bytes(uploaded_file.getvalue())
    final_path = obra_dir / f"{kind}{ext}"
    if final_path.exists():
        final_path.unlink()
    tmp_path.replace(final_path)
    return final_path.relative_to(BASE_DIR).as_posix()

def _download_btn_if_exists(label: str, path_str: str | None) -> None:
    if not path_str:
        return
    p = Path(path_str)
    if not p.is_absolute():
        p = BASE_DIR / p
    if p.exists() and p.is_file():
        st.download_button(label=label, data=p.read_bytes(), file_name=p.name, mime="application/octet-stream")

# CNPJ -> endereço
def buscar_cnpj_endereco(cnpj: str) -> dict | None:
    cnpj_limpo = re.sub(r"\D", "", cnpj or "")
    if len(cnpj_limpo) != 14:
        return None
    if requests is None:
        return None
    urls = [
        f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_limpo}",
        f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}",
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=6)
            if r.status_code == 200:
                data = r.json()
                logradouro = data.get("logradouro") or ""
                numero = data.get("numero") or ""
                bairro = data.get("bairro") or ""
                municipio = data.get("municipio") or data.get("cidade") or ""
                uf = data.get("uf") or data.get("estado") or ""
                cep = data.get("cep") or ""
                pedacos = [
                    logradouro.strip(),
                    numero.strip(),
                    bairro.strip(),
                    municipio.strip(),
                    uf.strip(),
                    f"CEP {cep.strip()}" if cep else "",
                ]
                endereco = ", ".join([p for p in pedacos if p])
                return {"endereco": endereco}
        except Exception:
            continue
    return None

# =============================================================================
# PDFs — com assinatura no campo do laboratorista
# =============================================================================
styles = getSampleStyleSheet()
styleN = styles["BodyText"]
styleSmall = ParagraphStyle("small", parent=styleN, fontSize=9, leading=11)
HB_ORANGE = colors.HexColor("#FF7A00")
FORM_CODE = "FORM.H.012.00"

def _header_vertical_centralizado() -> list:
    p1 = Paragraph("<b>Habisolute Engenharia e Controle Tecnológico</b>", ParagraphStyle("hdr1", parent=styleN, fontSize=11, alignment=TA_CENTER))
    p2 = Paragraph("contato@habisoluteengenharia.com.br", ParagraphStyle("hdr2", parent=styleN, fontSize=9, alignment=TA_CENTER))
    p3 = Paragraph("(16) 3877-9480", ParagraphStyle("hdr3", parent=styleN, fontSize=9, alignment=TA_CENTER))
    p4 = Paragraph(FORM_CODE, ParagraphStyle("hdr4", parent=styleN, fontSize=9, alignment=TA_CENTER))
    box = Table([[p1],[p2],[p3],[p4]], colWidths=[180*mm])
    box.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),0),
        ("BOTTOMPADDING",(0,0),(-1,-1),0),
        ("LEFTPADDING",(0,0),(-1,-1),0),
        ("RIGHTPADDING",(0,0),(-1,-1),0),
    ]))
    return [KeepTogether([box]), Spacer(1, 8)]

def _on_page(canvas, doc, titulo: str = ""):
    canvas.saveState()
    w, h = doc.pagesize
    canvas.setFillColor(HB_ORANGE)
    canvas.rect(0, h-10, w, 10, fill=1, stroke=0)
    canvas.setFillColor(HB_ORANGE)
    canvas.rect(0, 28, w, 2, fill=1, stroke=0)
    pagina = canvas.getPageNumber()
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    rod = f"Habisolute Engenharia — {FORM_CODE}  {agora}  pág. {pagina}"
    canvas.setFont("Helvetica", 8.5)
    tw = canvas.stringWidth(rod, "Helvetica", 8.5)
    canvas.setFillColor(colors.black)
    canvas.drawString((w - tw) / 2.0, 15, rod)
    canvas.restoreState()

def gerar_pdf_os(os_row, obra_row, itens: list[dict], show_prices: bool, logo_bytes: bytes | None, signature_bytes: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=28, bottomMargin=40, leftMargin=14, rightMargin=14)
    story = []
    story += _header_vertical_centralizado()

    # info da obra em caixa
    with SessionLocal() as sss:
        cli = sss.get(Cliente, obra_row.cliente_id) if obra_row.cliente_id else None

    info_data = [
        [Paragraph(f"<b>Status:</b> {os_row.status}", styleSmall)],
        [Paragraph(f"<b>Obra:</b> {obra_row.nome}", styleSmall)],
        [Paragraph(f"<b>Endereço:</b> {obra_row.endereco}", styleSmall)],
        [Paragraph(f"<b>Cliente:</b> {cli.nome if cli else (obra_row.cliente or '-')}", styleSmall)],
    ]
    info_tbl = Table(info_data, colWidths=[doc.width])
    info_tbl.setStyle(TableStyle([
        ("BOX", (0,0), (-1,-1), 0.6, colors.black),
        ("INNERGRID", (0,0), (-1,-1), 0.3, colors.grey),
        ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ("TOPPADDING",(0,0),(-1,-1),2),
        ("BOTTOMPADDING",(0,0),(-1,-1),2),
        ("LEFTPADDING",(0,0),(-1,-1),4),
        ("RIGHTPADDING",(0,0),(-1,-1),4),
    ]))
    story += [info_tbl, Spacer(1, 6)]

    titulo_os = f"ORDEM DE SERVIÇO Nº {os_row.numero}    DATA: {os_row.data_emissao.strftime('%d/%m/%Y')}"
    tit_tbl = Table(
        [[Paragraph(f"<b>{titulo_os}</b>", ParagraphStyle('titOS', parent=styleN, fontSize=11, alignment=TA_CENTER))]],
        colWidths=[doc.width],
    )
    tit_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")),
        ("BOX",(0,0),(-1,-1), 0.5, colors.black),
        ("TOPPADDING",(0,0),(-1,-1),6),
        ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ]))
    story += [tit_tbl, Spacer(1, 6)]

    headers = ["Código", "Descrição", "Un", "Qtd"]
    if show_prices:
        headers += ["Preço Unit", "Subtotal"]
    data_rows = [headers]
    for it in itens:
        row = [it["codigo"], it["descricao"], it["unidade"], f"{it['qtd_prev']:.2f}"]
        if show_prices:
            row += [format_brl(it["preco_unit"]), format_brl(it["subtotal"])]
        data_rows.append(row)

    W = doc.width
    col_widths = [0.16*W, 0.44*W, 0.06*W, 0.10*W, 0.12*W, 0.12*W] if show_prices else [0.18*W, 0.56*W, 0.08*W, 0.18*W]

    if show_prices:
        tot = sum(it["subtotal"] for it in itens)
        data_rows.append([""]*(len(headers)-2) + ["Total:", format_brl(tot)])

    tbl = Table(data_rows, colWidths=col_widths, repeatRows=1)
    base_style = [
        ("BACKGROUND", (0,0), (-1,0), colors.black),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID", (0,0), (-1,-1), 0.25, colors.black),
        ("TOPPADDING",(0,0),(-1,-1),3),
        ("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("ALIGN", (0,1), (1,-1), "LEFT"),
        ("ALIGN", (2,1), (2,-1), "CENTER"),
        ("ALIGN", (3,1), (3,-1), "RIGHT"),
    ]
    if show_prices:
        base_style.append(("ALIGN", (-2,1), (-1,-1), "RIGHT"))
        base_style.append(("FONTNAME", (-1,-1), (-1,-1), "Helvetica-Bold"))
    tbl.setStyle(TableStyle(base_style))
    story.append(tbl)
    story.append(Spacer(1, 16))
    story.append(Paragraph("Data: ____/____/______", ParagraphStyle("dt", parent=styleN, fontSize=10, alignment=TA_CENTER)))
    story.append(Spacer(1, 14))

    # assinatura: cliente + laboratorista com imagem
    if signature_bytes:
        sig_img = Image(io.BytesIO(signature_bytes))
        sig_img.drawHeight = 12 * mm
        sig_img.drawWidth = 50 * mm
        lab_cell = sig_img
    else:
        lab_cell = "_______________________________"

    ass_tbl = Table(
        [
            ["", "_______________________________", "", lab_cell, ""],
            ["", "Assinatura Cliente", "", "Assinatura Laboratorista", ""],
        ],
        colWidths=[10*mm, 70*mm, 15*mm, 70*mm, 10*mm],
    )
    ass_tbl.setStyle(TableStyle([
        ("ALIGN",(1,0),(1,0),"CENTER"),
        ("ALIGN",(3,0),(3,0),"CENTER"),
        ("ALIGN",(1,1),(1,1),"CENTER"),
        ("ALIGN",(3,1),(3,1),"CENTER"),
        ("TOPPADDING",(0,0),(-1,0),4),
        ("BOTTOMPADDING",(0,0),(-1,-1),0),
        ("LEFTPADDING",(0,0),(-1,-1),0),
        ("RIGHTPADDING",(0,0),(-1,-1),0),
    ]))
    story.append(ass_tbl)

    if signature_bytes:
        doc.build(story, onFirstPage=lambda c,d:_on_page(c,d,""), onLaterPages=lambda c,d:_on_page(c,d,""))
    else:
        doc.build(story, onFirstPage=lambda c,d:_on_page(c,d,""), onLaterPages=lambda c,d:_on_page(c,d,""))
    return buf.getvalue()

def gerar_pdf_medicao(obra_nome: str, periodo_str: str, linhas: list[dict], logo_bytes: bytes | None, medicao_num: int, signature_bytes: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=28, bottomMargin=40, leftMargin=14, rightMargin=14)
    story = []
    story += _header_vertical_centralizado()

    info_tbl = Table(
        [[Paragraph(f"<b>Obra:</b> {obra_nome}", styleSmall)],
         [Paragraph(f"<b>Período:</b> {periodo_str}", styleSmall)]],
        colWidths=[doc.width],
    )
    info_tbl.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.6,colors.black),
        ("INNERGRID",(0,0),(-1,-1),0.3,colors.grey),
        ("TOPPADDING",(0,0),(-1,-1),2),
        ("BOTTOMPADDING",(0,0),(-1,-1),2),
    ]))
    story += [info_tbl, Spacer(1, 6)]

    titulo = f"RELATÓRIO DE MEDIÇÃO — Medição nº {medicao_num}"
    tit_tbl = Table(
        [[Paragraph(f"<b>{titulo}</b>", ParagraphStyle("titMED", parent=styleN, fontSize=11, alignment=TA_CENTER))]],
        colWidths=[doc.width],
    )
    tit_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")),
        ("BOX",(0,0),(-1,-1),0.5,colors.black),
        ("TOPPADDING",(0,0),(-1,-1),6),
        ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ]))
    story += [tit_tbl, Spacer(1, 6)]

    headers = ["Data", "OS", "Código", "Descrição", "Un", "Qtd", "Preço", "Subtotal"]
    data_rows = [headers]
    for r in linhas:
        data_rows.append([
            r["data"].strftime("%d/%m/%Y") if isinstance(r["data"], date) else r["data"],
            r["os_num"],
            r["codigo"],
            r["descricao"],
            r["un"],
            f"{r['qtd']:.2f}",
            format_brl(r["preco"]),
            format_brl(r["subtotal"]),
        ])
    W = doc.width
    tbl = Table(
        data_rows,
        colWidths=[0.09*W, 0.14*W, 0.12*W, 0.31*W, 0.06*W, 0.08*W, 0.10*W, 0.10*W],
        repeatRows=1,
    )
    tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.black),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("GRID",(0,0),(-1,-1),0.25,colors.black),
        ("TOPPADDING",(0,0),(-1,-1),3),
        ("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("ALIGN",(0,1),(3,-1),"LEFT"),
        ("ALIGN",(4,1),(4,-1),"CENTER"),
        ("ALIGN",(5,1),(7,-1),"RIGHT"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 8))

    # resumo
    resumo = {}
    for r in linhas:
        key = (r["codigo"], r["descricao"], r["un"])
        acc = resumo.setdefault(key, {"qtd":0.0, "val":0.0})
        acc["qtd"] += float(r.get("qtd", 0.0) or 0.0)
        acc["val"] += float(r.get("subtotal", 0.0) or 0.0)
    story.append(Paragraph("<b>RESUMO DO PERÍODO</b>", ParagraphStyle("r", parent=styleN, alignment=TA_CENTER)))
    res_rows = [["Código", "Descrição", "Un", "Qtd", "Valor Total"]]
    for (cod, desc, un), acc in sorted(resumo.items(), key=lambda x: x[0][0]):
        res_rows.append([cod, desc, un, f"{acc['qtd']:.2f}", format_brl(acc["val"])])
    res_tbl = Table(res_rows, colWidths=[0.14*W, 0.46*W, 0.07*W, 0.13*W, 0.20*W], repeatRows=1)
    res_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.black),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("GRID",(0,0),(-1,-1),0.25,colors.black),
        ("ALIGN",(3,1),(4,-1),"RIGHT"),
    ]))
    story.append(res_tbl)
    story.append(Spacer(1, 6))

    total_val = sum(r["subtotal"] for r in linhas) if linhas else 0.0
    total_box = Table([[Paragraph("<b>Total:</b>", styleN), Paragraph(f"<b>{format_brl(total_val)}</b>", styleN)]], colWidths=[28*mm, 38*mm])
    total_box.setStyle(TableStyle([
        ("GRID",(0,0),(-1,-1),0.75,colors.black),
        ("ALIGN",(1,0),(1,0),"RIGHT"),
    ]))
    wrap = Table([[None, total_box]], colWidths=[doc.width - (28*mm+38*mm), (28*mm+38*mm)])
    story.append(wrap)

    doc.build(story, onFirstPage=lambda c,d:_on_page(c,d,titulo), onLaterPages=lambda c,d:_on_page(c,d,titulo))
    return buf.getvalue()
# ===================== PÁGINAS =====================

def page_clientes():
    st.markdown('<div class="section-title">Cadastro: Clientes</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    with SessionLocal() as sess:
        clientes = sess.query(Cliente).order_by(Cliente.nome.asc()).all()
    col_list, col_form = st.columns([1.2, 2.0])
    with col_list:
        lista = ["(Novo cliente)"] + [f"{c.id} — {c.nome}" for c in clientes]
        sel = st.selectbox("Selecione", lista, label_visibility="collapsed", key="cli_sel")
    with col_form:
        if sel != "(Novo cliente)":
            cli_id = int(sel.split("—", 1)[0].strip())
            with SessionLocal() as sess:
                cli = sess.get(Cliente, cli_id)
            nome = st.text_input("Nome", cli.nome)
            doc = st.text_input("Documento", cli.documento or "")
            contato = st.text_input("Contato", cli.contato or "")
            email = st.text_input("Email", cli.email or "")
            tel = st.text_input("Telefone", cli.telefone or "")
            ativo = st.checkbox("Ativo", value=(cli.ativo == 1))
            if st.button("Salvar cliente"):
                with SessionLocal() as sess:
                    c = sess.get(Cliente, cli_id)
                    c.nome = nome
                    c.documento = doc
                    c.contato = contato
                    c.email = email
                    c.telefone = tel
                    c.ativo = 1 if ativo else 0
                    sess.commit()
                st.success("Cliente atualizado.")
                _rerun()
        else:
            nome = st.text_input("Nome", "")
            doc = st.text_input("Documento", "")
            contato = st.text_input("Contato", "")
            email = st.text_input("Email", "")
            tel = st.text_input("Telefone", "")
            if st.button("Criar cliente"):
                with SessionLocal() as sess:
                    c = Cliente(
                        nome=nome,
                        documento=doc,
                        contato=contato,
                        email=email,
                        telefone=tel,
                        ativo=1,
                    )
                    sess.add(c)
                    sess.commit()
                st.success("Cliente criado.")
                _rerun()
    st.markdown("</div>", unsafe_allow_html=True)

def page_obras():
    st.markdown('<div class="section-title">Cadastro: Obras</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)

    with SessionLocal() as sess:
        obras = sess.query(Obra).order_by(Obra.nome.asc()).all()
        clientes = sess.query(Cliente).order_by(Cliente.nome.asc()).all()
        servicos_all = sess.query(Servico).filter(Servico.ativo == 1).order_by(Servico.descricao.asc()).all()

    col_list, col_form = st.columns([1.1, 2.4])

    with col_list:
        st.write("Obras existentes")
        nomes_obras = ["(Nova obra)"] + [f"{o.id} — {o.nome}" for o in obras]
        sel = st.selectbox("Selecione", nomes_obras, label_visibility="collapsed", key="obra_sel_combo")

    obra_edit = None
    if sel != "(Nova obra)":
        obra_id = int(sel.split("—", 1)[0].strip())
        with SessionLocal() as sess:
            obra_edit = sess.get(Obra, obra_id)

    with col_form:
        if obra_edit:
            st.subheader(f"Editar obra: {obra_edit.nome}")
            obra_nome = st.text_input("Nome da obra", obra_edit.nome)
            obra_end = st.text_area("Endereço", obra_edit.endereco or "", height=80)
            cli_nomes = ["(sem cliente)"] + [c.nome for c in clientes]
            cli_default = 0
            if obra_edit.cliente_id:
                for i, c in enumerate(clientes, start=1):
                    if c.id == obra_edit.cliente_id:
                        cli_default = i
                        break
            cli_sel = st.selectbox("Cliente", cli_nomes, index=cli_default)
            ativo = st.checkbox("Obra ativa", value=(obra_edit.ativo == 1))
            bloqueada = st.checkbox("Obra bloqueada", value=(obra_edit.bloqueada == 1))
            motivo_bloq = st.text_input("Motivo (se bloqueada)", obra_edit.bloqueada_motivo or "")

            cnpj_busca = st.text_input("CNPJ para buscar endereço", "")
            if st.button("Buscar endereço pelo CNPJ", key="btn_buscar_cnpj"):
                info = buscar_cnpj_endereco(cnpj_busca)
                if info and info.get("endereco"):
                    obra_end = info["endereco"]
                    st.success("Endereço preenchido pelo CNPJ.")
                else:
                    st.warning("Não consegui pegar o endereço desse CNPJ.")

            st.markdown("### Anexos da obra")
            up_prop = st.file_uploader("Proposta", key="up_prop")
            up_cont = st.file_uploader("Contrato", key="up_cont")
            up_cnpj  = st.file_uploader("CNPJ", key="up_cnpj")
            if up_prop is not None:
                rel = _save_anexo(up_prop, obra_edit.id, "proposta")
                with SessionLocal() as sess:
                    ob = sess.get(Obra, obra_edit.id); ob.anexo_proposta = rel; sess.commit()
                st.success("Proposta anexada.")
            if up_cont is not None:
                rel = _save_anexo(up_cont, obra_edit.id, "contrato")
                with SessionLocal() as sess:
                    ob = sess.get(Obra, obra_edit.id); ob.anexo_contrato = rel; sess.commit()
                st.success("Contrato anexado.")
            if up_cnpj is not None:
                rel = _save_anexo(up_cnpj, obra_edit.id, "cnpj")
                with SessionLocal() as sess:
                    ob = sess.get(Obra, obra_edit.id); ob.anexo_cnpj = rel; sess.commit()
                st.success("CNPJ anexado.")

            st.markdown("#### Arquivos já enviados")
            _download_btn_if_exists("Baixar proposta", obra_edit.anexo_proposta)
            _download_btn_if_exists("Baixar contrato", obra_edit.anexo_contrato)
            _download_btn_if_exists("Baixar CNPJ", obra_edit.anexo_cnpj)

            if st.button("Salvar alterações", key="btn_save_obra"):
                with SessionLocal() as sess:
                    ob = sess.get(Obra, obra_edit.id)
                    ob.nome = obra_nome
                    ob.endereco = obra_end
                    if cli_sel != "(sem cliente)":
                        cli_obj = sess.query(Cliente).filter(Cliente.nome == cli_sel).first()
                        ob.cliente_id = cli_obj.id if cli_obj else None
                        ob.cliente = cli_obj.nome if cli_obj else None
                    else:
                        ob.cliente_id = None
                        ob.cliente = None
                    ob.ativo = 1 if ativo else 0
                    ob.bloqueada = 1 if bloqueada else 0
                    ob.bloqueada_motivo = motivo_bloq
                    if bloqueada and not ob.bloqueada_desde:
                        ob.bloqueada_desde = date.today()
                    sess.commit()
                st.success("Obra atualizada.")
                _rerun()
        else:
            st.subheader("Nova obra")
            obra_nome = st.text_input("Nome da obra", "")
            obra_end = st.text_area("Endereço", "", height=80)
            cli_nomes = ["(sem cliente)"] + [c.nome for c in clientes]
            cli_sel = st.selectbox("Cliente", cli_nomes, index=0)
            if st.button("Salvar nova obra", key="btn_nova_obra"):
                with SessionLocal() as sess:
                    nova = Obra(
                        nome=obra_nome,
                        endereco=obra_end,
                        cliente=cli_sel if cli_sel != "(sem cliente)" else None,
                        ativo=1,
                    )
                    if cli_sel != "(sem cliente)":
                        cli_obj = sess.query(Cliente).filter(Cliente.nome == cli_sel).first()
                        if cli_obj:
                            nova.cliente_id = cli_obj.id
                    sess.add(nova); sess.commit()
                st.success("Obra criada.")
                _rerun()

    st.markdown('</div>', unsafe_allow_html=True)

    # serviços específicos da obra
    if sel != "(Nova obra)" and obra_edit:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### Serviços específicos dessa obra")
        with SessionLocal() as sess:
            obra_servs = sess.query(ObraServico).filter(
                ObraServico.obra_id == obra_edit.id,
                ObraServico.ativo == 1
            ).all()
            servicos_all = sess.query(Servico).filter(Servico.ativo == 1).order_by(Servico.descricao.asc()).all()

        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            srv_options = [f"{s.id} — {s.codigo} — {s.descricao}" for s in servicos_all]
            srv_sel = st.selectbox("Serviço", srv_options, key="obra_srv_sel")
        with c2:
            preco_espec = st.number_input("Preço específico", min_value=0.0, value=0.0, step=1.0, format="%.2f")
        with c3:
            st.write("")
            if st.button("Vincular/atualizar serviço", key="btn_vinc_srv"):
                srv_id = int(srv_sel.split("—", 1)[0].strip())
                with SessionLocal() as sess:
                    osrv = sess.query(ObraServico).filter(
                        ObraServico.obra_id == obra_edit.id,
                        ObraServico.servico_id == srv_id
                    ).first()
                    if osrv:
                        osrv.preco_unit = preco_espec
                        osrv.ativo = 1
                    else:
                        osrv = ObraServico(
                            obra_id=obra_edit.id,
                            servico_id=srv_id,
                            preco_unit=preco_espec,
                            ativo=1
                        )
                        sess.add(osrv)
                    sess.commit()
                st.success("Serviço vinculado/atualizado.")
                _rerun()

        if obra_servs:
            rows = []
            for osrv in obra_servs:
                serv_desc = next((f"{s.codigo} — {s.descricao}" for s in servicos_all if s.id == osrv.servico_id), str(osrv.servico_id))
                rows.append({"Serviço": serv_desc, "Preço específico": osrv.preco_unit or 0.0})
            df_os = pd.DataFrame(rows)
            st.dataframe(df_os, use_container_width=True)
        else:
            st.info("Nenhum serviço específico vinculado.")
        st.markdown('</div>', unsafe_allow_html=True)

def page_servicos():
    st.markdown('<div class="section-title">Cadastro: Serviços</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    with SessionLocal() as sess:
        servicos = sess.query(Servico).order_by(Servico.codigo.asc()).all()
    col_list, col_form = st.columns([1.1, 2.4])
    with col_list:
        ops = ["(Novo serviço)"] + [f"{s.id} — {s.codigo} — {s.descricao}" for s in servicos]
        sel = st.selectbox("Serviços", ops, label_visibility="collapsed")
    with col_form:
        if sel != "(Novo serviço)":
            sv_id = int(sel.split("—", 1)[0].strip())
            with SessionLocal() as sess:
                sv = sess.get(Servico, sv_id)
            codigo = st.text_input("Código", sv.codigo)
            desc = st.text_input("Descrição", sv.descricao)
            un = st.text_input("Unidade", sv.unidade or "un")
            preco = st.number_input("Preço unitário padrão", min_value=0.0, value=float(sv.preco_unit or 0.0), step=1.0, format="%.2f")
            ativo = st.checkbox("Ativo", value=(sv.ativo == 1))
            if st.button("Salvar serviço"):
                with SessionLocal() as sess:
                    s2 = sess.get(Servico, sv_id)
                    s2.codigo = codigo
                    s2.descricao = desc
                    s2.unidade = un
                    s2.preco_unit = preco
                    s2.ativo = 1 if ativo else 0
                    sess.commit()
                st.success("Serviço atualizado.")
                _rerun()
        else:
            codigo = st.text_input("Código", "")
            desc = st.text_input("Descrição", "")
            un = st.text_input("Unidade", "un")
            preco = st.number_input("Preço unitário padrão", min_value=0.0, value=0.0, step=1.0, format="%.2f")
            if st.button("Criar serviço"):
                with SessionLocal() as sess:
                    sv = Servico(
                        codigo=codigo,
                        descricao=desc,
                        unidade=un,
                        preco_unit=preco,
                        ativo=1,
                    )
                    sess.add(sv); sess.commit()
                st.success("Serviço criado.")
                _rerun()
    st.markdown('</div>', unsafe_allow_html=True)

def obter_os_com_itens(sess: Session, os_id: int):
    os_row = sess.query(OS).options(selectinload(OS.itens).selectinload(OSItem.servico)).filter(OS.id == os_id).first()
    obra_row = sess.get(Obra, os_row.obra_id)
    itens = []
    for it in os_row.itens:
        sv = it.servico
        preco = it.preco_unit if getattr(it, "preco_unit", None) is not None else (sv.preco_unit or 0.0)
        itens.append({
            "codigo": sv.codigo,
            "descricao": sv.descricao,
            "unidade": sv.unidade,
            "qtd_prev": it.quantidade_prevista or 0.0,
            "preco_unit": preco,
            "subtotal": preco * (it.quantidade_prevista or 0.0),
        })
    return os_row, obra_row, itens

def page_emitir_os():
    st.markdown('<div class="section-title">Emitir OS</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    with SessionLocal() as sess:
        obras = sess.query(Obra).filter(Obra.ativo == 1).order_by(Obra.nome.asc()).all()
        servicos = sess.query(Servico).filter(Servico.ativo == 1).order_by(Servico.descricao.asc()).all()

    # escolher OS para editar ou nova
    with SessionLocal() as sess:
        os_list = sess.query(OS).order_by(OS.id.desc()).limit(50).all()
    ops = ["(Nova OS)"] + [f"{o.id} — {o.numero} — {o.status}" for o in os_list]
    os_sel = st.selectbox("Selecione OS", ops, key="os_sel_emitir")

    if os_sel != "(Nova OS)":
        os_id = int(os_sel.split("—", 1)[0].strip())
        with SessionLocal() as sess:
            os_db = sess.get(OS, os_id)
        modo_novo = False
    else:
        os_db = None
        modo_novo = True

    # formulário
    obra_opc = [f"{o.id} — {o.nome}" for o in obras]
    if modo_novo:
        obra_idx = 0
        os_numero = ""
        os_status = "Aberta"
        os_obs = ""
        data_emissao = date.today()
    else:
        obra_idx = 0
        for i, o in enumerate(obras):
            if o.id == os_db.obra_id:
                obra_idx = i
                break
        os_numero = os_db.numero
        os_status = os_db.status
        os_obs = os_db.observacoes or ""
        data_emissao = os_db.data_emissao or date.today()

    obra_sel = st.selectbox("Obra", obra_opc, index=obra_idx if obra_opc else 0)
    st.date_input("Data de emissão", value=data_emissao, key="emit_os_dt")
    st.selectbox("Status", STATUS_OPTIONS, index=STATUS_OPTIONS.index(os_status) if os_status in STATUS_OPTIONS else 0, key="emit_os_status")
    os_obs_new = st.text_area("Observações", os_obs, height=110)

    # itens da os
    st.markdown("Itens da OS")
    col_s, col_q, col_p, col_btn = st.columns([2.8, 1, 1, 0.4])
    with col_s:
        srv_ops = [f"{s.id} — {s.codigo} — {s.descricao}" for s in servicos]
        srv_sel = st.selectbox("Serviço", srv_ops, key="emit_os_srv")
    with col_q:
        qtd = st.number_input("Qtd", min_value=0.0, value=1.0, step=1.0, format="%.2f")
    with col_p:
        preco_in = st.number_input("Preço unit.", min_value=0.0, value=0.0, step=1.0, format="%.2f")
    with col_btn:
        st.write("")
        add_item = st.button("➕", key="btn_add_item_os")

    # salvar OS
    if st.button("Salvar OS", use_container_width=True):
        obra_id = int(obra_sel.split("—", 1)[0].strip())
        with SessionLocal() as sess:
            if modo_novo:
                num = gerar_numero_os(sess)
                nova = OS(
                    numero=num,
                    data_emissao=s["emit_os_dt"],
                    obra_id=obra_id,
                    status=s["emit_os_status"],
                    observacoes=os_obs_new,
                )
                sess.add(nova)
                sess.commit()
                os_id_new = nova.id
            else:
                os_obj = sess.get(OS, os_db.id)
                os_obj.data_emissao = s["emit_os_dt"]
                os_obj.obra_id = obra_id
                os_obj.status = s["emit_os_status"]
                os_obj.observacoes = os_obs_new
                sess.commit()
                os_id_new = os_obj.id
        st.success("OS salva.")
        _rerun()

    # adicionar item se for OS já existente
    if add_item and not modo_novo:
        srv_id = int(srv_sel.split("—", 1)[0].strip())
        obra_id = int(obra_sel.split("—", 1)[0].strip())
        with SessionLocal() as sess:
            os_obj = sess.get(OS, os_db.id)
            # pegar preço específico da obra
            ospec = (
                sess.query(ObraServico)
                .filter(ObraServico.obra_id == obra_id, ObraServico.servico_id == srv_id, ObraServico.ativo == 1)
                .first()
            )
            sv = sess.get(Servico, srv_id)
            preco_final = preco_in or (ospec.preco_unit if ospec else (sv.preco_unit or 0.0))
            item = OSItem(
                os_id=os_obj.id,
                servico_id=sv.id,
                quantidade_prevista=qtd,
                preco_unit=preco_final,
            )
            sess.add(item); sess.commit()
        st.success("Item incluído.")
        _rerun()

    # mostrar itens da OS quando editando
    if not modo_novo:
        with SessionLocal() as sess:
            os_row, obra_row, itens = obter_os_com_itens(sess, os_db.id)
        if itens:
            df_it = pd.DataFrame(itens).rename(columns={
                "codigo":"Código",
                "descricao":"Descrição",
                "unidade":"Un",
                "qtd_prev":"Qtd",
                "preco_unit":"Preço unit.",
                "subtotal":"Subtotal",
            })
            st.dataframe(df_it, use_container_width=True)
        else:
            st.info("Esta OS ainda não tem itens.")
    st.markdown('</div>', unsafe_allow_html=True)

def page_visualizar_imprimir():
    st.markdown('<div class="section-title">Visualizar / Imprimir</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    with SessionLocal() as sess:
        os_df_full = to_df(sess, OS)
        obras_map = {o.id: f"{o.nome} — {o.endereco}" for o in sess.query(Obra).all()}
        clientes_map = {c.id: c.nome for c in sess.query(Cliente).all()}
    if os_df_full.empty:
        banner("info", "Nenhuma OS emitida.")
        st.markdown('</div>', unsafe_allow_html=True)
        return
    os_df_full["data_emissao"] = pd.to_datetime(os_df_full["data_emissao"], errors="coerce").dt.date
    os_df_full["obra_nome"] = os_df_full["obra_id"].map(lambda oid: obras_map.get(oid, f"Obra {oid}"))
    os_df_full["data_str"] = os_df_full["data_emissao"].apply(lambda d: d.strftime("%d/%m/%Y") if isinstance(d, date) else "")
    f1, f2 = st.columns([2,1])
    obra_opcoes = ["(Todas)"] + sorted(os_df_full["obra_nome"].dropna().unique().tolist())
    obra_filtro = f1.selectbox("Filtrar por obra", obra_opcoes)
    status_opcoes = ["(Todos)"] + STATUS_OPTIONS
    status_filtro = f2.selectbox("Status", status_opcoes)
    min_dt = os_df_full["data_emissao"].min() or date.today()
    max_dt = os_df_full["data_emissao"].max() or date.today()
    periodo = st.date_input("Período", value=(min_dt, max_dt))
    ini, fim = periodo
    df_view = os_df_full.copy()
    if obra_filtro != "(Todas)":
        df_view = df_view[df_view["obra_nome"] == obra_filtro]
    if status_filtro != "(Todos)":
        df_view = df_view[df_view["status"] == status_filtro]
    df_view = df_view[(df_view["data_emissao"] >= ini) & (df_view["data_emissao"] <= fim)].sort_values(["data_emissao","id"], ascending=[False, False])

    q = st.text_input("Buscar por número da OS", "").strip().upper()
    if q:
        df_view = df_view[df_view["numero"].str.contains(q, na=False, case=False)]
    if df_view.empty:
        banner("warn", "Nenhuma OS encontrada com os filtros.")
        st.markdown('</div>', unsafe_allow_html=True)
        return

    df_view["label"] = df_view.apply(lambda r: f"{r['numero']} — {r['obra_nome']} — {r['data_str']} [{r['status']}]", axis=1)
    labels = df_view["label"].tolist()
    idx = st.selectbox("Selecione a OS", labels, index=0)
    row = df_view[df_view["label"] == idx].iloc[0]

    with SessionLocal() as sess:
        os_row_db = sess.query(OS).filter(OS.id == int(row["id"])).first()
        os_row, obra_row, itens = obter_os_com_itens(sess, os_row_db.id)

    # informações
    st.write(f"**OS:** {os_row.numero}")
    st.write(f"**Data:** {os_row.data_emissao.strftime('%d/%m/%Y')}")
    st.write(f"**Obra:** {obra_row.nome}")
    st.write(f"**Endereço:** {obra_row.endereco}")
    with SessionLocal() as s2:
        cli = s2.get(Cliente, obra_row.cliente_id) if obra_row.cliente_id else None
    st.write(f"**Cliente:** {(cli.nome if cli else (obra_row.cliente or '-'))}")
    if os_row.observacoes:
        st.write(f"**Observações:** {os_row.observacoes}")

    total = sum(it["subtotal"] for it in itens)
    st.markdown(f"<div class='card'><b>Total estimado</b><div style='font-size:1.4rem;margin-top:.35rem'>{format_brl(total)}</div></div>", unsafe_allow_html=True)

    if itens:
        df_itens = pd.DataFrame(itens).rename(columns={
            "codigo":"Código","descricao":"Descrição","unidade":"Un","qtd_prev":"Qtd Prevista","preco_unit":"Preço Unit.","subtotal":"Subtotal"
        })
        st.dataframe(df_itens[["Código","Descrição","Un","Qtd Prevista","Preço Unit.","Subtotal"]], use_container_width=True)
    else:
        banner("info", "Esta OS não possui itens.")

    sig_bytes = load_signature_bytes()
    pdf_interno = gerar_pdf_os(os_row, obra_row, itens, show_prices=True, logo_bytes=None, signature_bytes=sig_bytes)
    pdf_cliente = gerar_pdf_os(os_row, obra_row, itens, show_prices=False, logo_bytes=None, signature_bytes=sig_bytes)
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("Baixar PDF (interno — com preços)", data=pdf_interno, file_name=f"{os_row.numero}_interno.pdf", mime="application/pdf")
    with c2:
        st.download_button("Baixar PDF (cliente — sem preços)", data=pdf_cliente, file_name=f"{os_row.numero}_cliente.pdf", mime="application/pdf")

    st.markdown('</div>', unsafe_allow_html=True)

def page_medicao():
    st.markdown('<div class="section-title">Medição Mensal</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    with SessionLocal() as sess:
        obras = sess.query(Obra).order_by(Obra.nome.asc()).all()
        os_todos = sess.query(OS).order_by(OS.data_emissao.desc()).all()
    obra_ops = [f"{o.id} — {o.nome}" for o in obras]
    if not obra_ops:
        banner("info", "Cadastre obras primeiro.")
        st.markdown('</div>', unsafe_allow_html=True)
        return
    obra_sel = st.selectbox("Obra", obra_ops)
    obra_id = int(obra_sel.split("—", 1)[0].strip())
    periodo = st.date_input("Período da medição", value=(date.today().replace(day=1), date.today()))
    ini, fim = periodo
    # puxar OS da obra dentro do período
    linhas = []
    with SessionLocal() as sess:
        os_obra = (
            sess.query(OS)
            .filter(OS.obra_id == obra_id, OS.data_emissao >= ini, OS.data_emissao <= fim)
            .order_by(OS.data_emissao.asc())
            .all()
        )
        for os_row in os_obra:
            os_row, obra_row, itens = obter_os_com_itens(sess, os_row.id)
            for it in itens:
                linhas.append({
                    "data": os_row.data_emissao,
                    "os_num": os_row.numero,
                    "codigo": it["codigo"],
                    "descricao": it["descricao"],
                    "un": it["unidade"],
                    "qtd": it["qtd_prev"],
                    "preco": it["preco_unit"],
                    "subtotal": it["subtotal"],
                })
    if linhas:
        df_med = pd.DataFrame(linhas)
        st.dataframe(df_med, use_container_width=True)
        sig_bytes = load_signature_bytes()
        pdf = gerar_pdf_medicao(
            obra_nome=obra_sel.split("—", 1)[1].strip(),
            periodo_str=f"{ini.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}",
            linhas=linhas,
            logo_bytes=None,
            medicao_num=1,
            signature_bytes=sig_bytes,
        )
        st.download_button("Baixar PDF da medição", data=pdf, file_name=f"medicao_{obra_id}_{ini:%Y%m}.pdf", mime="application/pdf")
    else:
        st.info("Nenhuma OS dessa obra no período.")
    st.markdown('</div>', unsafe_allow_html=True)
def page_relatorios():
    st.markdown('<div class="section-title">Relatórios</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    with SessionLocal() as sess:
        clientes = sess.query(Cliente).order_by(Cliente.nome.asc()).all()
    cli_ops = [f"{c.id} — {c.nome}" for c in clientes]
    if not cli_ops:
        banner("info", "Cadastre clientes para emitir relatórios.")
        st.markdown('</div>', unsafe_allow_html=True)
        return
    cli_sel = st.selectbox("Cliente", cli_ops)
    cli_id = int(cli_sel.split("—", 1)[0].strip())
    periodo = st.date_input("Período", value=(date.today().replace(day=1), date.today()))
    ini, fim = periodo
    linhas = []
    with SessionLocal() as sess:
        obras_cli = sess.query(Obra).filter(Obra.cliente_id == cli_id).all()
        ids_obras = [o.id for o in obras_cli]
        os_rows = (
            sess.query(OS)
            .filter(OS.obra_id.in_(ids_obras), OS.data_emissao >= ini, OS.data_emissao <= fim)
            .order_by(OS.data_emissao.asc())
            .all()
        )
        for os_row in os_rows:
            os_row, obra_row, itens = obter_os_com_itens(sess, os_row.id)
            for it in itens:
                linhas.append({
                    "data": os_row.data_emissao,
                    "obra": obra_row.nome,
                    "codigo": it["codigo"],
                    "descricao": it["descricao"],
                    "un": it["unidade"],
                    "qtd": it["qtd_prev"],
                    "preco": it["preco_unit"],
                    "subtotal": it["subtotal"],
                })
    if linhas:
        df_rel = pd.DataFrame(linhas)
        st.dataframe(df_rel, use_container_width=True)
        from_name = cli_sel.split("—", 1)[1].strip()
        sig_bytes = load_signature_bytes()
        pdf = gerar_pdf_fechamento(
            cliente_nome=from_name,
            periodo_str=f"{ini.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}",
            linhas=linhas,
            logo_bytes=None,
            signature_bytes=sig_bytes,
        )
        st.download_button("Baixar PDF de fechamento", data=pdf, file_name=f"fechamento_{cli_id}_{ini:%Y%m}.pdf", mime="application/pdf")
    else:
        st.info("Nada a mostrar nesse período.")
    st.markdown('</div>', unsafe_allow_html=True)

def gerar_pdf_fechamento(cliente_nome: str, periodo_str: str, linhas: list[dict], logo_bytes: bytes | None, signature_bytes: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=28, bottomMargin=40, leftMargin=14, rightMargin=14)
    story = []
    story += _header_vertical_centralizado()
    info_tbl = Table([[Paragraph(f"<b>Cliente:</b> {cliente_nome}", styleSmall)],
                      [Paragraph(f"<b>Período:</b> {periodo_str}", styleSmall)]], colWidths=[doc.width])
    info_tbl.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.6,colors.black),
        ("INNERGRID",(0,0),(-1,-1),0.3,colors.grey),
        ("TOPPADDING",(0,0),(-1,-1),2),
        ("BOTTOMPADDING",(0,0),(-1,-1),2),
    ]))
    story += [info_tbl, Spacer(1, 6)]
    titulo = "FECHAMENTO POR CLIENTE"
    tit_tbl = Table([[Paragraph(f"<b>{titulo}</b>", ParagraphStyle("titFEC", parent=styleN, fontSize=11, alignment=TA_CENTER))]], colWidths=[doc.width])
    tit_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")),
        ("BOX",(0,0),(-1,-1),0.5,colors.black),
        ("TOPPADDING",(0,0),(-1,-1),6),
        ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ]))
    story += [tit_tbl, Spacer(1, 6)]

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
    tbl = Table(rows, colWidths=[0.28*W, 0.10*W, 0.34*W, 0.06*W, 0.10*W, 0.12*W], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0), colors.black),
        ("TEXTCOLOR",(0,0),(-1,0), colors.white),
        ("GRID",(0,0),(-1,-1),0.25,colors.black),
        ("ALIGN",(3,1),(5,-1),"RIGHT"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 8))

    total_box = Table([[Paragraph("<b>Total geral:</b>", styleN), Paragraph(f"<b>{format_brl(total)}</b>", styleN)]], colWidths=[36*mm, 42*mm])
    total_box.setStyle(TableStyle([
        ("GRID",(0,0),(-1,-1),0.75,colors.black),
        ("ALIGN",(1,0),(1,0),"RIGHT"),
    ]))
    wrap = Table([[None, total_box]], colWidths=[doc.width-(36*mm+42*mm), (36*mm+42*mm)])
    story.append(wrap)

    doc.build(story, onFirstPage=lambda c,d:_on_page(c,d,titulo), onLaterPages=lambda c,d:_on_page(c,d,titulo))
    return buf.getvalue()

# ====== EXPORTAÇÃO COM EXCEL (fallback CSV) ======
def make_os_excel_per_obras() -> tuple[bytes, str, str]:
    with SessionLocal() as sess:
        os_rows = sess.query(OS).order_by(OS.data_emissao.desc()).all()
        obras = {o.id: o for o in sess.query(Obra).all()}
        clientes = {c.id: c for c in sess.query(Cliente).all()}
    data = []
    for o in os_rows:
        obra = obras.get(o.obra_id)
        cli = clientes.get(obra.cliente_id) if obra and obra.cliente_id else None
        data.append({
            "OS": o.numero,
            "Data emissão": o.data_emissao.strftime("%d/%m/%Y") if o.data_emissao else "",
            "Status": o.status,
            "Obra": obra.nome if obra else "",
            "Endereço": obra.endereco if obra else "",
            "Cliente": cli.nome if cli else (obra.cliente if obra else ""),
        })
    df = pd.DataFrame(data)
    # tentar excel
    try:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl", datetime_format="DD/MM/YYYY") as writer:
            df.to_excel(writer, sheet_name="OS", index=False)
        return output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "os_por_obras.xlsx"
    except Exception:
        # fallback para CSV
        return df.to_csv(index=False).encode("utf-8-sig"), "text/csv", "os_por_obras.csv"

def page_export():
    st.markdown('<div class="section-title">Exportações</div>', unsafe_allow_html=True)

    with st.expander("Backup (DB + anexos)", expanded=False):
        if st.button("Gerar backup ZIP", key="btn_backup_zip"):
            p = make_full_backup()
            st.download_button("Baixar backup", data=p.read_bytes(), file_name=p.name, mime="application/zip")

    with st.expander("Exportar OS por obra (Excel/CSV)", expanded=True):
        data, mime, fname = make_os_excel_per_obras()
        st.download_button("Baixar planilha", data=data, file_name=fname, mime=mime)

    with st.expander("Assinatura digital (PDF)", expanded=True):
        st.write("Envie uma imagem de assinatura (PNG/JPG) para carimbar nos PDFs gerados.")
        up = st.file_uploader("Imagem da assinatura", type=["png","jpg","jpeg"])
        if up is not None:
            if save_signature_file(up):
                banner("success", "Assinatura salva! Os próximos PDFs já saem assinados.")
        sig = load_signature_bytes()
        if sig:
            st.image(sig, caption="Assinatura atual", width=180)

# ===================== MENU / ROUTER =====================
st.sidebar.markdown("###  Sistema OS", unsafe_allow_html=True)
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
