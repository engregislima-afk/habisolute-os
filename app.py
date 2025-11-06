# -*- coding: utf-8 -*-
# Habisolute — Sistema de OS (Streamlit)
# versão: OS + PDF assinado + cadastro obras completo + exportar excel
# atualizado: emitir OS mostra itens + visualizar imprime itens + busca CNPJ mais completa

import io, re, os, json, base64, tempfile, zipfile, hashlib, calendar, secrets
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

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
    select
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
s.setdefault("current_os_id", None)  # <- pra manter a OS aberta depois do rerun

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
# CSS — degradê laranja no sidebar + campos com borda
# =============================================================================
def _inject_css(theme: str | None = None):
    mode = (theme or st.session_state.get("theme_mode") or "Claro").strip().lower()
    if mode == "claro":
        HB_BG, HB_CARD, HB_BORDER, HB_TEXT, HB_MUTED, HB_GLASS = "#f3f4f6", "#ffffff", "#dbe2ea", "#0f172a", "#475569", "rgba(15,23,42,.02)"
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
}}
[data-testid="stAppViewContainer"] {{
  background: var(--hb-bg)!important;
}}
[data-testid="stSidebar"] {{
  background: radial-gradient(circle at top, rgba(249,115,22,.45) 0%, rgba(249,115,22,0) 50%), linear-gradient(180deg, #2f3137 0%, #d1d5db 100%) !important;
  border-right: 1px solid rgba(148,163,184,.25);
}}
.stTextInput input, .stTextArea textarea, .stNumberInput input, .stDateInput input, .stSelectbox [data-baseweb="select"] > div {{
  border: 1px solid rgba(148,163,184,.55)!important;
  border-radius: 10px!important;
  background: rgba(255,255,255,.92)!important;
}}
.stDownloadButton>button, .stButton>button {{
  border-radius: 12px!important;
}}
.hb-topbar {{
  height:6px; background: linear-gradient(90deg, var(--hb-accent), #ffb267);
  border-radius:6px; margin:4px 0 10px 0;
}}
.hb-card {{
  background: rgba(255,255,255,.95); border:1px solid rgba(148,163,184,.30); border-radius:18px; padding:16px; margin-bottom:14px;
}}
.hb-alert {{
  background: rgba(219,234,254,0.6); border-left:6px solid #2563eb; border-radius:14px; padding:.65rem 1rem; margin-top:1rem;
}}
.hb-alert-warn {{
  background: rgba(254,249,195,.6); border-left:6px solid #eab308;
}}
.hb-alert-success {{
  background: rgba(220,252,231,.6); border-left:6px solid #22c55e;
}}
</style>
""", unsafe_allow_html=True)

_inject_css()

def banner(kind: str, text: str):
    cls = "hb-alert"
    if kind == "warn":
        cls = "hb-alert hb-alert-warn"
    elif kind == "success":
        cls = "hb-alert hb-alert-success"
    st.markdown(f"<div class='{cls}'>{text}</div>", unsafe_allow_html=True)

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
    st.markdown(f"<div class='hb-card'><b>🏗️ {SYSTEM_NAME}</b></div>", unsafe_allow_html=True)

# assinatura digital
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
def _auth_login_ui():
    st.markdown("<div class='hb-card'>", unsafe_allow_html=True)
    st.markdown("<h4>🔐 Entrar</h4>", unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        user = st.text_input("Usuário", key="login_user")
    with col2:
        pwd = st.text_input("Senha", key="login_pass", type="password")
    if st.button("Acessar", use_container_width=True):
        rec = user_get((user or "").strip())
        if not rec or not rec.get("active", True):
            flash("warn", "Usuário inexistente ou inativo.")
        elif _hash_password_simple(pwd) != rec.get("password"):
            flash("error", "Senha incorreta.")
        else:
            s["logged_in"]  = True
            s["username"]   = (user or "").strip()
            s["is_admin"]   = bool(rec.get("is_admin", False))
            s["role"]       = rec.get("role", "usuario")
            s["must_change"] = bool(rec.get("must_change", False))
            prefs = load_user_prefs()
            prefs["last_user"] = s["username"]
            save_user_prefs(prefs)
            flash("success", f"Bem-vindo, {s['username']}!")
            _rerun()
    st.markdown("</div>", unsafe_allow_html=True)

def _force_change_password_ui(username: str):
    st.markdown("<div class='hb-card'>", unsafe_allow_html=True)
    st.markdown("<h4>🔑 Definir nova senha</h4>", unsafe_allow_html=True)
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
            s["must_change"] = False
            flash("success", "Senha alterada!")
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
st.markdown(f"<div class='hb-card'>👋 Olá, <b>{nome_login}</b></div>", unsafe_allow_html=True)

# toolbar topo
col_t1, col_t3 = st.columns([1, 1])
with col_t1:
    s["theme_mode"] = st.radio("Tema", ["Claro", "Escuro"], horizontal=True,
                               index=0 if s.get("theme_mode") == "Claro" else 1, key="theme_sel_main")
with col_t3:
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
    endereco = Column(String)  # <<< novo
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
    documento = Column(String)  # CNPJ/CPF
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

# garantir colunas antigas em obras
def _ensure_obras_extra(engine):
    with engine.begin() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info('obras')").fetchall()}
        if "anexo_proposta" not in cols:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN anexo_proposta TEXT")
        if "anexo_contrato" not in cols:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN anexo_contrato TEXT")
        if "anexo_cnpj" not in cols:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN anexo_cnpj TEXT")
        if "documento" not in cols:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN documento TEXT")
_ensure_obras_extra(engine)

# índices
with engine.begin() as conn:
    try:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
    try:
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_os_obra_data ON os(obra_id, data_emissao);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_os_status ON os(status);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_os_numero ON os(numero);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_ositem_osid ON os_itens(os_id);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_medicoes_obra ON medicoes(obra_id);")
    except Exception:
        pass

STATUS_OPTIONS = ["Aberta", "Em Execução", "Medido em Aberto", "Medido", "Concluída", "Cancelada"]

# =============================================================================
# Helpers de negócio
# =============================================================================
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

# -------------- BUSCA CNPJ mais completa
def buscar_cnpj_detalhado(cnpj: str) -> dict | None:
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
                logradouro = data.get("logradouro") or data.get("descricao_tipo_logradouro") or ""
                numero = data.get("numero") or ""
                bairro = data.get("bairro") or ""
                municipio = data.get("municipio") or data.get("cidade") or ""
                uf = data.get("uf") or data.get("estado") or ""
                cep = data.get("cep") or ""
                endereco = ", ".join([p for p in [
                    logradouro.strip(), numero.strip(), bairro.strip(),
                    municipio.strip(), uf.strip(),
                    f"CEP {cep.strip()}" if cep else ""
                ] if p])
                return {
                    "razao_social": data.get("razao_social") or data.get("nome") or "",
                    "nome_fantasia": data.get("nome_fantasia") or "",
                    "email": data.get("email") or "",
                    "telefone": data.get("telefone") or "",
                    "endereco": endereco,
                }
        except Exception:
            continue
    return None
# =============================================================================
# PDFs helpers (cabeçalhos) — igual ao seu original
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

    doc.build(story, onFirstPage=lambda c,d:_on_page(c,d,""), onLaterPages=lambda c,d:_on_page(c,d,""))
    return buf.getvalue()

# =============================================================================
# Função que carrega OS + itens (usada nas 2 telas)
# =============================================================================
def obter_os_com_itens(sess: Session, os_id: int):
    os_row = (
        sess.query(OS)
        .options(selectinload(OS.itens).selectinload(OSItem.servico))
        .filter(OS.id == os_id)
        .first()
    )
    if not os_row:
        return None, None, []
    obra_row = sess.get(Obra, os_row.obra_id)
    itens = []
    for it in os_row.itens:
        sv = it.servico
        preco = it.preco_unit if it.preco_unit is not None else (sv.preco_unit or 0.0)
        qtd = it.quantidade_prevista or 0.0
        itens.append({
            "codigo": sv.codigo,
            "descricao": sv.descricao,
            "unidade": sv.unidade,
            "qtd_prev": qtd,
            "preco_unit": preco,
            "subtotal": preco * qtd,
        })
    return os_row, obra_row, itens

# =============================================================================
# PÁGINA: CLIENTES
# =============================================================================
def page_clientes():
    st.markdown("<h4>Cadastro: Clientes</h4>", unsafe_allow_html=True)
    st.markdown("<div class='hb-card'>", unsafe_allow_html=True)
    with SessionLocal() as sess:
        clientes = sess.query(Cliente).order_by(Cliente.nome.asc()).all()
    col_list, col_form = st.columns([1.1, 2.0])
    with col_list:
        lista = ["(Novo cliente)"] + [f"{c.id} — {c.nome}" for c in clientes]
        sel = st.selectbox("Selecione", lista, label_visibility="collapsed", key="cli_sel")
    with col_form:
        if sel != "(Novo cliente)":
            cli_id = int(sel.split("—", 1)[0].strip())
            with SessionLocal() as sess:
                cli = sess.get(Cliente, cli_id)
            nome = st.text_input("Nome / Razão social", cli.nome)
            doc = st.text_input("Documento (CNPJ/CPF)", cli.documento or "")
            end = st.text_area("Endereço", cli.endereco or "", height=70)
            contato = st.text_input("Contato", cli.contato or "")
            email = st.text_input("Email", cli.email or "")
            tel = st.text_input("Telefone", cli.telefone or "")
            ativo = st.checkbox("Ativo", value=(cli.ativo == 1))

            if st.button("Buscar CNPJ e preencher", key="btn_cli_busca_cnpj"):
                info = buscar_cnpj_detalhado(doc)
                if info:
                    if info.get("razao_social"):
                        nome = info["razao_social"]
                    if info.get("endereco"):
                        end = info["endereco"]
                    if info.get("email"):
                        email = info["email"]
                    if info.get("telefone"):
                        tel = info["telefone"]
                    st.success("Dados preenchidos pelo CNPJ. Clique em salvar.")
                else:
                    st.warning("Não consegui buscar esse CNPJ agora.")

            if st.button("Salvar cliente", key="btn_cli_save"):
                with SessionLocal() as sess:
                    c = sess.get(Cliente, cli_id)
                    c.nome = nome
                    c.documento = doc
                    c.endereco = end
                    c.contato = contato
                    c.email = email
                    c.telefone = tel
                    c.ativo = 1 if ativo else 0
                    sess.commit()
                flash("success", "Cliente atualizado.")
                _rerun()
        else:
            nome = st.text_input("Nome / Razão social", key="cli_nome_new")
            doc = st.text_input("Documento (CNPJ/CPF)", key="cli_doc_new")
            end = st.text_area("Endereço", key="cli_end_new", height=70)
            contato = st.text_input("Contato", key="cli_cont_new")
            email = st.text_input("Email", key="cli_email_new")
            tel = st.text_input("Telefone", key="cli_tel_new")

            if st.button("Buscar CNPJ e preencher", key="btn_cli_busca_cnpj_new"):
                info = buscar_cnpj_detalhado(st.session_state.get("cli_doc_new",""))
                if info:
                    if info.get("razao_social"):
                        st.session_state["cli_nome_new"] = info["razao_social"]
                    if info.get("endereco"):
                        st.session_state["cli_end_new"] = info["endereco"]
                    if info.get("email"):
                        st.session_state["cli_email_new"] = info["email"]
                    if info.get("telefone"):
                        st.session_state["cli_tel_new"] = info["telefone"]
                    st.success("Dados preenchidos pelo CNPJ. Clique em salvar.")

            if st.button("Criar cliente", key="btn_cli_create"):
                with SessionLocal() as sess:
                    c = Cliente(
                        nome=st.session_state.get("cli_nome_new") or "Cliente",
                        documento=st.session_state.get("cli_doc_new") or "",
                        endereco=st.session_state.get("cli_end_new") or "",
                        contato=st.session_state.get("cli_cont_new") or "",
                        email=st.session_state.get("cli_email_new") or "",
                        telefone=st.session_state.get("cli_tel_new") or "",
                        ativo=1,
                    )
                    sess.add(c); sess.commit()
                flash("success", "Cliente criado.")
                _rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# =============================================================================
# PÁGINA: OBRAS (com anexos e documento)
# =============================================================================
def page_obras():
    st.markdown("<h4>Cadastro: Obras</h4>", unsafe_allow_html=True)
    st.markdown("<div class='hb-card'>", unsafe_allow_html=True)
    with SessionLocal() as sess:
        obras = sess.query(Obra).order_by(Obra.nome.asc()).all()
        clientes = sess.query(Cliente).order_by(Cliente.nome.asc()).all()
        servicos_all = sess.query(Servico).filter(Servico.ativo == 1).order_by(Servico.descricao.asc()).all()

    col_list, col_form = st.columns([1.1, 2.4])
    with col_list:
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
            doc_obra = st.text_input("Documento da obra (CNPJ/CPF)", obra_edit.documento or "")

            if st.button("Buscar CNPJ e preencher obra", key="btn_buscar_cnpj_obra_edit"):
                info = buscar_cnpj_detalhado(doc_obra)
                if info and info.get("endereco"):
                    obra_end = info["endereco"]
                    st.success("Endereço preenchido pelo CNPJ.")
                elif not info:
                    st.warning("Não consegui pegar o endereço desse CNPJ.")

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

            st.markdown("### Anexos da obra")
            up_prop = st.file_uploader("Proposta", key="up_prop")
            up_cont = st.file_uploader("Contrato", key="up_cont")
            up_cnpj  = st.file_uploader("Cartão CNPJ", key="up_cnpj")
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
                    ob.documento = doc_obra
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
            doc_obra = st.text_input("Documento da obra (CNPJ/CPF)", "")
            if st.button("Buscar CNPJ e preencher obra", key="btn_buscar_cnpj_obra_new"):
                info = buscar_cnpj_detalhado(doc_obra)
                if info and info.get("endereco"):
                    obra_end = info["endereco"]
                    st.success("Endereço preenchido.")
                else:
                    st.warning("Não consegui pegar o endereço desse CNPJ.")
            cli_nomes = ["(sem cliente)"] + [c.nome for c in clientes]
            cli_sel = st.selectbox("Cliente", cli_nomes, index=0)
            if st.button("Salvar nova obra", key="btn_nova_obra"):
                with SessionLocal() as sess:
                    nova = Obra(
                        nome=obra_nome,
                        endereco=obra_end,
                        cliente=cli_sel if cli_sel != "(sem cliente)" else None,
                        documento=doc_obra,
                        ativo=1,
                    )
                    if cli_sel != "(sem cliente)":
                        cli_obj = sess.query(Cliente).filter(Cliente.nome == cli_sel).first()
                        if cli_obj:
                            nova.cliente_id = cli_obj.id
                    sess.add(nova); sess.commit()
                st.success("Obra criada.")
                _rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    # vincular preço específico
    if sel != "(Nova obra)" and obra_edit:
        st.markdown("<div class='hb-card'>", unsafe_allow_html=True)
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
            st.dataframe(df_os, use_container_width=True, hide_index=True)
        else:
            st.info("Nenhum serviço específico vinculado.")
        st.markdown("</div>", unsafe_allow_html=True)

# =============================================================================
# PÁGINA: SERVIÇOS
# =============================================================================
def page_servicos():
    st.markdown("<h4>Cadastro: Serviços</h4>", unsafe_allow_html=True)
    st.markdown("<div class='hb-card'>", unsafe_allow_html=True)
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
    st.markdown("</div>", unsafe_allow_html=True)

# =============================================================================
# PÁGINA: EMITIR OS (corrigida)
# =============================================================================
def page_emitir_os():
    st.markdown("<h4>Emitir OS</h4>", unsafe_allow_html=True)
    st.markdown("<div class='hb-card'>", unsafe_allow_html=True)

    # carregar combos
    with SessionLocal() as sess:
        obras = sess.query(Obra).filter(Obra.ativo == 1).order_by(Obra.nome.asc()).all()
        servicos = sess.query(Servico).filter(Servico.ativo == 1).order_by(Servico.descricao.asc()).all()
        os_list = sess.query(OS).order_by(OS.id.desc()).limit(200).all()

    ops_os = ["(Nova OS)"] + [f"{o.id} — {o.numero} — {o.status}" for o in os_list]
    sel_os = st.selectbox("Selecione OS", ops_os, key="os_sel_emitir")

    modo_novo = (sel_os == "(Nova OS)")
    os_db = None

    if not modo_novo:
        os_id = int(sel_os.split("—", 1)[0].strip())
        s["current_os_id"] = os_id
        with SessionLocal() as sess:
            os_db = sess.get(OS, os_id)

    obra_opc = [f"{o.id} — {o.nome}" for o in obras] or ["(cadastre obras)"]

    if modo_novo:
        obra_idx = 0
        data_emissao = date.today()
        os_status = "Aberta"
        os_obs = ""
    else:
        # achar índice da obra
        obra_idx = 0
        for i, o in enumerate(obras):
            if o.id == os_db.obra_id:
                obra_idx = i
                break
        data_emissao = os_db.data_emissao or date.today()
        os_status = os_db.status
        os_obs = os_db.observacoes or ""

    obra_sel = st.selectbox("Obra", obra_opc, index=obra_idx if obra_opc else 0)
    dt_os = st.date_input("Data de emissão", value=data_emissao, key="emit_os_dt")
    status_sel = st.selectbox("Status", STATUS_OPTIONS, index=STATUS_OPTIONS.index(os_status) if os_status in STATUS_OPTIONS else 0, key="emit_os_status")
    obs_txt = st.text_area("Observações", os_obs, height=120)

    # linha de itens
    st.markdown("### Itens da OS")
    col_s, col_q, col_p, col_btn = st.columns([2.8, 1, 1, 0.4])
    with col_s:
        srv_ops = [f"{s.id} — {s.codigo} — {s.descricao}" for s in servicos]
        srv_sel = st.selectbox("Serviço", srv_ops, key="emit_os_srv")
    with col_q:
        qtd = st.number_input("Qtd", min_value=0.0, value=1.0, step=1.0, format="%.2f", key="emit_os_qtd")
    with col_p:
        preco_in = st.number_input("Preço unit.", min_value=0.0, value=0.0, step=1.0, format="%.2f", key="emit_os_preco")
    with col_btn:
        st.write("")
        add_item = st.button("➕", key="btn_add_item_os")

    # botão salvar OS
    if st.button("Salvar OS", use_container_width=True, key="btn_save_os_main"):
        obra_id = int(obra_sel.split("—", 1)[0].strip())
        with SessionLocal() as sess:
            if modo_novo:
                num = gerar_numero_os(sess)
                nova = OS(
                    numero=num,
                    data_emissao=dt_os,
                    obra_id=obra_id,
                    status=status_sel,
                    observacoes=obs_txt,
                )
                sess.add(nova); sess.commit()
                s["current_os_id"] = nova.id
                flash("success", f"OS {num} criada. Agora você pode incluir os serviços.")
            else:
                os_obj = sess.get(OS, os_db.id)
                os_obj.data_emissao = dt_os
                os_obj.obra_id = obra_id
                os_obj.status = status_sel
                os_obj.observacoes = obs_txt
                sess.commit()
                s["current_os_id"] = os_obj.id
                flash("success", f"OS {os_obj.numero} atualizada.")
        _rerun()

    # adicionar item (só se já existe OS salva)
    if add_item:
        if not s.get("current_os_id"):
            flash("warn", "Salve a OS primeiro para poder incluir itens.")
            _rerun()
        else:
            os_id_current = s["current_os_id"]
            srv_id = int(srv_sel.split("—", 1)[0].strip())
            obra_id = int(obra_sel.split("—", 1)[0].strip())
            with SessionLocal() as sess:
                os_obj = sess.get(OS, os_id_current)
                # preço específico da obra
                ospec = (
                    sess.query(ObraServico)
                    .filter(ObraServico.obra_id == obra_id,
                            ObraServico.servico_id == srv_id,
                            ObraServico.ativo == 1)
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
            flash("success", "Serviço adicionado à OS.")
            _rerun()

    st.markdown("</div>", unsafe_allow_html=True)

    # tabela de itens já adicionados
    st.markdown("<div class='hb-card'>", unsafe_allow_html=True)
    st.markdown("### Serviços já adicionados a esta OS", unsafe_allow_html=True)
    if s.get("current_os_id"):
        with SessionLocal() as sess:
            os_row, obra_row, itens = obter_os_com_itens(sess, s["current_os_id"])
        if itens:
            df_it = pd.DataFrame(itens).rename(columns={
                "codigo":"Código",
                "descricao":"Descrição",
                "unidade":"Un",
                "qtd_prev":"Qtd",
                "preco_unit":"Preço unit.",
                "subtotal":"Subtotal",
            })
            st.dataframe(df_it, use_container_width=True, hide_index=True)
            total = sum(i["subtotal"] for i in itens)
            st.markdown(f"<div class='hb-alert-success'><b>Total da OS: {format_brl(total)}</b></div>", unsafe_allow_html=True)
        else:
            st.markdown("<div class='hb-alert'>Esta OS ainda não tem itens. Adicione usando o botão ➕.</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='hb-alert'>Salve a OS primeiro para poder incluir e ver os serviços.</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)
# =============================================================================
# PÁGINA: VISUALIZAR / IMPRIMIR (corrigida)
# =============================================================================
def page_visualizar_imprimir():
    st.markdown("<h4>Visualizar / Imprimir</h4>", unsafe_allow_html=True)
    st.markdown("<div class='hb-card'>", unsafe_allow_html=True)
    with SessionLocal() as sess:
        os_df_full = to_df(sess, OS)
        obras_map = {o.id: f"{o.nome} — {o.endereco}" for o in sess.query(Obra).all()}
        clientes_map = {c.id: c.nome for c in sess.query(Cliente).all()}
    if os_df_full.empty:
        banner("info", "Nenhuma OS emitida.")
        st.markdown("</div>", unsafe_allow_html=True)
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
        st.markdown("</div>", unsafe_allow_html=True)
        return

    df_view["label"] = df_view.apply(lambda r: f"{r['numero']} — {r['obra_nome']} — {r['data_str']} [{r['status']}]", axis=1)
    labels = df_view["label"].tolist()
    idx = st.selectbox("Selecione a OS", labels, index=0)
    row = df_view[df_view["label"] == idx].iloc[0]

    with SessionLocal() as sess:
        os_row_db = sess.query(OS).filter(OS.id == int(row["id"])).first()
        os_row, obra_row, itens = obter_os_com_itens(sess, os_row_db.id)
        cli = sess.get(Cliente, obra_row.cliente_id) if obra_row and obra_row.cliente_id else None

    st.write(f"**OS:** {os_row.numero}")
    st.write(f"**Data:** {os_row.data_emissao.strftime('%d/%m/%Y')}")
    st.write(f"**Obra:** {obra_row.nome}")
    st.write(f"**Endereço:** {obra_row.endereco}")
    st.write(f"**Cliente:** {(cli.nome if cli else (obra_row.cliente or '-'))}")
    if os_row.observacoes:
        st.write(f"**Observações:** {os_row.observacoes}")

    total = sum(it["subtotal"] for it in itens)
    st.markdown(f"<div class='hb-card'><b>Total estimado</b><div style='font-size:1.4rem;margin-top:.35rem'>{format_brl(total)}</div></div>", unsafe_allow_html=True)

    if itens:
        df_itens = pd.DataFrame(itens).rename(columns={
            "codigo":"Código","descricao":"Descrição","unidade":"Un","qtd_prev":"Qtd Prevista","preco_unit":"Preço Unit.","subtotal":"Subtotal"
        })
        st.dataframe(df_itens[["Código","Descrição","Un","Qtd Prevista","Preço Unit.","Subtotal"]], use_container_width=True, hide_index=True)
    else:
        st.markdown("<div class='hb-alert'>Esta OS não possui itens.</div>", unsafe_allow_html=True)

    sig_bytes = load_signature_bytes()
    pdf_interno = gerar_pdf_os(os_row, obra_row, itens, show_prices=True, logo_bytes=None, signature_bytes=sig_bytes)
    pdf_cliente = gerar_pdf_os(os_row, obra_row, itens, show_prices=False, logo_bytes=None, signature_bytes=sig_bytes)
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("Baixar PDF (interno — com preços)", data=pdf_interno, file_name=f"{os_row.numero}_interno.pdf", mime="application/pdf")
    with c2:
        st.download_button("Baixar PDF (cliente — sem preços)", data=pdf_cliente, file_name=f"{os_row.numero}_cliente.pdf", mime="application/pdf")

    st.markdown("</div>", unsafe_allow_html=True)

# =============================================================================
# PÁGINA: MEDIÇÃO MENSAL (mesma lógica do seu original)
# =============================================================================
def page_medicao():
    st.markdown("<h4>Medição Mensal</h4>", unsafe_allow_html=True)
    st.markdown("<div class='hb-card'>", unsafe_allow_html=True)
    with SessionLocal() as sess:
        obras = sess.query(Obra).order_by(Obra.nome.asc()).all()
    if not obras:
        banner("info", "Cadastre obras primeiro.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    obra_ops = [f"{o.id} — {o.nome}" for o in obras]
    obra_sel = st.selectbox("Obra", obra_ops)
    obra_id = int(obra_sel.split("—", 1)[0].strip())
    periodo = st.date_input("Período da medição", value=(date.today().replace(day=1), date.today()))
    ini, fim = periodo
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
        st.dataframe(df_med, use_container_width=True, hide_index=True)
        # pode reaproveitar seu PDF de medição se quiser
    else:
        st.info("Nenhuma OS dessa obra no período.")
    st.markdown("</div>", unsafe_allow_html=True)

# =============================================================================
# PÁGINA: RELATÓRIOS — mantive simples
# =============================================================================
def page_relatorios():
    st.markdown("<h4>Relatórios</h4>", unsafe_allow_html=True)
    st.markdown("<div class='hb-card'>Em construção leve — já dá pra usar Visualizar/Imprimir para quase tudo.</div>", unsafe_allow_html=True)

# =============================================================================
# EXPORTAÇÕES
# =============================================================================
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
    try:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl", datetime_format="DD/MM/YYYY") as writer:
            df.to_excel(writer, sheet_name="OS", index=False)
        return output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "os_por_obras.xlsx"
    except Exception:
        return df.to_csv(index=False).encode("utf-8-sig"), "text/csv", "os_por_obras.csv"

def page_export():
    st.markdown("<h4>Exportações</h4>", unsafe_allow_html=True)
    st.markdown("<div class='hb-card'>", unsafe_allow_html=True)
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
    st.markdown("</div>", unsafe_allow_html=True)

# =============================================================================
# MENU / ROUTER
# =============================================================================
st.sidebar.markdown("###  Sistema OS", unsafe_allow_html=True)
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
