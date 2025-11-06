# -*- coding: utf-8 -*-
from __future__ import annotations

import io, re, os, json, zipfile, hashlib
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

import streamlit as st
import pandas as pd

# tentar ter requests para CNPJ
try:
    import requests
except Exception:
    requests = None

# SQLAlchemy
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date, ForeignKey, Text, select
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session, selectinload

# ReportLab
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

# ======================================================================
# CONFIG
# ======================================================================
SYSTEM_NAME = "Habisolute — Sistema de OS"
SYSTEM_CODE = "hab_os"
BRAND_COLOR = "#f97316"

st.set_page_config(page_title=SYSTEM_NAME, layout="wide")

BASE_DIR   = Path(__file__).resolve().parent
PREFS_DIR  = BASE_DIR / f".{SYSTEM_CODE}"; PREFS_DIR.mkdir(parents=True, exist_ok=True)
USERS_DB   = PREFS_DIR / "users.json"
AUDIT_LOG  = PREFS_DIR / "audit.jsonl"
PREFS_PATH = PREFS_DIR / "prefs.json"
SIGNATURE_PATH = PREFS_DIR / "signature.png"

# ======================================================================
# PREFS
# ======================================================================
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

# ======================================================================
# FLASH
# ======================================================================
def banner(kind: str, text: str):
    icon = {"success":"✅", "error":"⛔", "warn":"⚠️", "info":"ℹ️"}.get(kind, "ℹ️")
    st.markdown(
        f"""<div style="display:flex;gap:.5rem;align-items:flex-start;background:rgba(248,250,252,.9);border-left:5px solid #f97316;border-radius:14px;padding:.35rem .6rem;margin:.35rem 0;">
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

# ======================================================================
# STATE
# ======================================================================
s = st.session_state
s.setdefault("logged_in", False)
s.setdefault("username", None)
s.setdefault("is_admin", False)
s.setdefault("role", "usuario")
s.setdefault("must_change", False)
s.setdefault("theme_mode", load_user_prefs().get("theme_mode", "Claro"))
s.setdefault("_flash", [])
# este é o cara que vai manter a OS corrente
s.setdefault("current_os_id", None)

def _rerun():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass

# ======================================================================
# AUTH
# ======================================================================
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

# ======================================================================
# CSS
# ======================================================================
def _inject_css():
    mode = (st.session_state.get("theme_mode") or "Claro").lower()
    if mode == "claro":
        HB_BG, HB_CARD, HB_TEXT = "#f3f4f6", "#ffffff", "#0f172a"
    else:
        HB_BG, HB_CARD, HB_TEXT = "#0f1116", "#141821", "#f8fafc"

    st.markdown(f"""
<style>
html, body, [data-testid="stAppViewContainer"] {{
  background: {HB_BG}!important;
  color: {HB_TEXT}!important;
}}
[data-testid="stSidebar"] {{
  background: radial-gradient(circle at top, rgba(249,115,22,.5) 0%, rgba(249,115,22,0) 50%), linear-gradient(180deg, #2f3137 0%, #d1d5db 100%) !important;
}}
.card {{
  background:{HB_CARD};
  border-radius:18px;
  padding:16px;
  margin-bottom:14px;
}}
.section-title {{
  background:linear-gradient(90deg, #f97316, #ffb267);
  border-radius:12px;
  padding:.6rem .8rem;
  text-align:center;
  font-weight:800;
  margin-bottom:12px;
  color:#111;
}}
/* moldura dos campos */
.stTextInput > div > div > input,
.stTextArea > div > textarea,
.stNumberInput input,
.stDateInput input,
.stSelectbox > div[data-baseweb="select"] > div {{
  border:1px solid #cbd5e1 !important;
  border-radius:12px !important;
  background:#fff !important;
  color:{HB_TEXT} !important;
}}
.stTextArea > div > textarea {{
  min-height:80px;
}}
</style>
""", unsafe_allow_html=True)

_inject_css()

# ======================================================================
# LOGIN UI
# ======================================================================
def _auth_login_ui():
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.markdown("<div class='section-title'>🔐 Entrar</div>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1.3, 1.3, .7])
    with col1:
        user = st.text_input("Usuário", key="login_user")
    with col2:
        pwd = st.text_input("Senha", key="login_pass", type="password")
    with col3:
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
    st.markdown("<div class='section-title'>🔑 Nova senha</div>", unsafe_allow_html=True)
    p1 = st.text_input("Nova senha", type="password")
    p2 = st.text_input("Confirmar", type="password")
    if st.button("Salvar senha"):
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
            flash("success", "Senha alterada.")
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

# topo
st.markdown("<div style='height:6px;background:linear-gradient(90deg,#f97316,#ffb267);border-radius:6px;margin-bottom:10px;'></div>", unsafe_allow_html=True)
st.markdown(f"<div class='card'><b>🏗️ {SYSTEM_NAME}</b></div>", unsafe_allow_html=True)
nome_login = s.get("username") or load_user_prefs().get("last_user") or "—"
st.markdown(f"<div class='card'>👋 Olá, <b>{nome_login}</b></div>", unsafe_allow_html=True)

# toolbar
c1, _, c3 = st.columns([1,1,1])
with c1:
    s["theme_mode"] = st.radio("Tema", ["Claro","Escuro"], index=0 if s["theme_mode"]=="Claro" else 1, horizontal=True, key="theme_sel_main")
with c3:
    st.write("")
    if st.button("Sair", use_container_width=True):
        s["logged_in"] = False
        flash("info", "Sessão encerrada.")
        _rerun()

if "theme_prev" not in s:
    s["theme_prev"] = s["theme_mode"]
if s["theme_mode"] != s["theme_prev"]:
    prefs = load_user_prefs(); prefs["theme_mode"] = s["theme_mode"]; save_user_prefs(prefs)
    s["theme_prev"] = s["theme_mode"]
    _rerun()

# ======================================================================
# DB / MODELOS
# ======================================================================
Base = declarative_base()

class Cliente(Base):
    __tablename__ = "clientes"
    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False, unique=True)
    documento = Column(String)
    endereco = Column(String)
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
    documento = Column(String)  # CNPJ/CPF
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

def _ensure_extra(engine):
    with engine.begin() as conn:
        cols_c = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info('clientes')").fetchall()}
        if "endereco" not in cols_c:
            conn.exec_driver_sql("ALTER TABLE clientes ADD COLUMN endereco TEXT")
        cols_o = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info('obras')").fetchall()}
        if "documento" not in cols_o:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN documento TEXT")
        if "anexo_proposta" not in cols_o:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN anexo_proposta TEXT")
        if "anexo_contrato" not in cols_o:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN anexo_contrato TEXT")
        if "anexo_cnpj" not in cols_o:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN anexo_cnpj TEXT")
_ensure_extra(engine)

STATUS_OPTIONS = ["Aberta", "Em Execução", "Medido em Aberto", "Medido", "Concluída", "Cancelada"]

# ======================================================================
# BACKUP / ANEXOS
# ======================================================================
BACKUPS_DIR = BASE_DIR / "backups"; BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
ANEXOS_DIR = BASE_DIR / "anexos" / "obras"; ANEXOS_DIR.mkdir(parents=True, exist_ok=True)
_VALID_KINDS = {"cnpj","proposta","contrato"}

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

def _save_anexo(uploaded_file, obra_id: int, kind: str) -> str | None:
    if uploaded_file is None:
        return None
    kind = kind.lower().strip()
    if kind not in _VALID_KINDS:
        return None
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
    if p.exists():
        st.download_button(label, data=p.read_bytes(), file_name=p.name, mime="application/octet-stream")

# ======================================================================
# ASSINATURA
# ======================================================================
def save_signature_file(uploaded_file) -> bool:
    if uploaded_file is None:
        return False
    SIGNATURE_PATH.write_bytes(uploaded_file.getvalue())
    return True

def load_signature_bytes() -> bytes | None:
    if SIGNATURE_PATH.exists():
        return SIGNATURE_PATH.read_bytes()
    return None

# ======================================================================
# CNPJ LOOKUP
# ======================================================================
def buscar_cnpj_endereco(cnpj: str) -> dict | None:
    cnpj_limpo = re.sub(r"\D", "", cnpj or "")
    if len(cnpj_limpo) != 14 or requests is None:
        return None

    urls = [
        f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_limpo}",
        f"https://publica.cnpj.ws/cnpj/{cnpj_limpo}",
        f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}",
    ]

    for url in urls:
        try:
            r = requests.get(url, timeout=7)
            if r.status_code != 200:
                continue
            data = r.json()

            razao = (
                data.get("razao_social")
                or data.get("nome")
                or data.get("razao")
                or data.get("nome_empresarial")
                or ""
            )
            fantasia = data.get("nome_fantasia") or data.get("fantasia") or ""

            est = data.get("estabelecimento") or {}

            logradouro = data.get("logradouro") or est.get("logradouro") or ""
            numero     = data.get("numero")     or est.get("numero")     or ""
            bairro     = data.get("bairro")     or est.get("bairro")     or ""
            municipio  = (
                data.get("municipio")
                or data.get("cidade")
                or est.get("cidade_nome")
                or est.get("cidade")
                or ""
            )
            uf         = data.get("uf") or est.get("estado") or ""
            cep        = data.get("cep") or est.get("cep") or ""

            partes = []
            if logradouro: partes.append(logradouro.strip())
            if numero:     partes.append(numero.strip())
            if bairro:     partes.append(bairro.strip())
            if municipio:  partes.append(municipio.strip())
            if uf:         partes.append(uf.strip())
            if cep:        partes.append(f"CEP {cep.strip()}")
            endereco = ", ".join(partes)

            tel = data.get("telefone") or est.get("telefone") or est.get("telefone1")
            if not tel:
                ddd1 = data.get("ddd_telefone_1")
                tel1 = data.get("telefone_1")
                if ddd1 and tel1:
                    tel = f"({ddd1}) {tel1}"
            email = data.get("email") or est.get("email") or ""

            return {
                "razao_social": razao,
                "nome_fantasia": fantasia,
                "endereco": endereco,
                "email": email,
                "telefone": tel or "",
                "_raw": data,
            }
        except Exception:
            continue
    return None

# ======================================================================
# CALLBACKS CNPJ
# ======================================================================
def cb_buscar_cnpj_cliente_novo():
    info = buscar_cnpj_endereco(st.session_state.get("cli_doc_new", ""))
    if not info:
        st.session_state["cli_busca_msg"] = "Não consegui consultar esse CNPJ agora."
        return
    st.session_state["cli_nome_new"] = info.get("razao_social") or info.get("nome_fantasia") or st.session_state.get("cli_nome_new", "")
    st.session_state["cli_end_new"] = info.get("endereco") or st.session_state.get("cli_end_new", "")
    st.session_state["cli_email_new"] = info.get("email") or st.session_state.get("cli_email_new", "")
    st.session_state["cli_tel_new"] = info.get("telefone") or st.session_state.get("cli_tel_new", "")
    st.session_state["cli_busca_msg"] = "Dados do CNPJ carregados."

def cb_buscar_cnpj_cliente_edit():
    info = buscar_cnpj_endereco(st.session_state.get("cli_doc", ""))
    if not info:
        st.session_state["cli_busca_msg_edit"] = "Não consegui consultar esse CNPJ agora."
        return
    st.session_state["cli_nome"] = info.get("razao_social") or info.get("nome_fantasia") or st.session_state.get("cli_nome", "")
    st.session_state["cli_end"] = info.get("endereco") or st.session_state.get("cli_end", "")
    st.session_state["cli_email"] = info.get("email") or st.session_state.get("cli_email", "")
    st.session_state["cli_tel"] = info.get("telefone") or st.session_state.get("cli_tel", "")
    st.session_state["cli_busca_msg_edit"] = "Dados do CNPJ carregados."

def cb_buscar_cnpj_obra_nova():
    info = buscar_cnpj_endereco(st.session_state.get("obra_doc_new", ""))
    if not info:
        st.session_state["obra_busca_msg_new"] = "Não consegui consultar esse CNPJ agora."
        return
    st.session_state["obra_end_new"] = info.get("endereco") or st.session_state.get("obra_end_new", "")
    st.session_state["obra_busca_msg_new"] = "Endereço preenchido pelo CNPJ."

def cb_buscar_cnpj_obra_edit(id_obra: int):
    key_doc = f"obra_doc_edit_{id_obra}"
    key_end = f"obra_end_edit_{id_obra}"
    msg_key = f"obra_busca_msg_edit_{id_obra}"
    info = buscar_cnpj_endereco(st.session_state.get(key_doc, ""))
    if not info:
        st.session_state[msg_key] = "Não consegui consultar esse CNPJ agora."
        return
    st.session_state[key_end] = info.get("endereco") or st.session_state.get(key_end, "")
    st.session_state[msg_key] = "Endereço preenchido pelo CNPJ."

# ======================================================================
# PDF helpers
# ======================================================================
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
    box.setStyle(TableStyle([("ALIGN",(0,0),(-1,-1),"CENTER"),("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
    return [KeepTogether([box]), Spacer(1, 8)]

def _on_page(canvas, doc, titulo: str = ""):
    canvas.saveState()
    w, h = doc.pagesize
    canvas.setFillColor(HB_ORANGE)
    canvas.rect(0, h-10, w, 10, fill=1, stroke=0)
    canvas.rect(0, 28, w, 2, fill=1, stroke=0)
    pagina = canvas.getPageNumber()
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    rod = f"Habisolute Engenharia — {FORM_CODE}  {agora}  pág. {pagina}"
    canvas.setFont("Helvetica", 8.5)
    tw = canvas.stringWidth(rod, "Helvetica", 8.5)
    canvas.setFillColor(colors.black)
    canvas.drawString((w - tw) / 2.0, 15, rod)
    canvas.restoreState()

def format_brl(v: float) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"

def gerar_pdf_os(os_row, obra_row, itens: list[dict], show_prices: bool, logo_bytes: bytes | None, signature_bytes: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=28, bottomMargin=40, leftMargin=14, rightMargin=14)
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
    info_tbl.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.6,colors.black)]))
    story += [info_tbl, Spacer(1, 6)]

    titulo_os = f"ORDEM DE SERVIÇO Nº {os_row.numero}    DATA: {os_row.data_emissao.strftime('%d/%m/%Y')}"
    tit_tbl = Table([[Paragraph(f"<b>{titulo_os}</b>", ParagraphStyle('t', parent=styleN, fontSize=11, alignment=TA_CENTER))]], colWidths=[doc.width])
    tit_tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")),("BOX",(0,0),(-1,-1), 0.5, colors.black)]))
    story += [tit_tbl, Spacer(1, 6)]

    headers = ["Código", "Descrição", "Un", "Qtd"]
    if show_prices:
        headers += ["Preço Unit", "Subtotal"]
    data_rows = [headers]
    for it in itens:
        base = [it["codigo"], it["descricao"], it["unidade"], f"{it['qtd_prev']:.2f}"]
        if show_prices:
            base += [format_brl(it["preco_unit"]), format_brl(it["subtotal"])]
        data_rows.append(base)

    W = doc.width
    col_widths = [0.16*W, 0.44*W, 0.06*W, 0.10*W, 0.12*W, 0.12*W] if show_prices else [0.18*W, 0.56*W, 0.08*W, 0.18*W]
    if show_prices:
        tot = sum(it["subtotal"] for it in itens)
        data_rows.append([""]*(len(headers)-2) + ["Total:", format_brl(tot)])

    tbl = Table(data_rows, colWidths=col_widths, repeatRows=1)
    base_style = [
        ("BACKGROUND",(0,0),(-1,0),colors.black),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("GRID",(0,0),(-1,-1),0.25,colors.black),
        ("ALIGN",(2,1),(3,-1),"RIGHT"),
    ]
    if show_prices:
        base_style.append(("ALIGN",(-2,1),(-1,-1),"RIGHT"))
    tbl.setStyle(TableStyle(base_style))
    story.append(tbl)
    story.append(Spacer(1, 14))

    if signature_bytes:
        sig_img = Image(io.BytesIO(signature_bytes)); sig_img.drawHeight = 12*mm; sig_img.drawWidth = 50*mm
        lab_cell = sig_img
    else:
        lab_cell = "_______________________________"
    ass_tbl = Table(
        [["", "_______________________________", "", lab_cell, ""],
         ["", "Assinatura Cliente", "", "Assinatura Laboratorista", ""]],
        colWidths=[10*mm, 70*mm, 15*mm, 70*mm, 10*mm],
    )
    story.append(ass_tbl)

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
    info_tbl.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.6,colors.black)]))
    story += [info_tbl, Spacer(1, 6)]

    titulo = f"RELATÓRIO DE MEDIÇÃO — Medição nº {medicao_num}"
    tit_tbl = Table(
        [[Paragraph(f"<b>{titulo}</b>", ParagraphStyle("tm", parent=styleN, fontSize=11, alignment=TA_CENTER))]],
        colWidths=[doc.width],
    )
    tit_tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")),("BOX",(0,0),(-1,-1),0.5,colors.black)]))
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
    tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.black),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),0.25,colors.black)]))
    story.append(tbl)
    story.append(Spacer(1, 8))

    resumo = {}
    for r in linhas:
        key = (r["codigo"], r["descricao"], r["un"])
        acc = resumo.setdefault(key, {"qtd": 0.0, "val": 0.0})
        acc["qtd"] += float(r.get("qtd", 0.0) or 0.0)
        acc["val"] += float(r.get("subtotal", 0.0) or 0.0)
    story.append(Paragraph("<b>RESUMO DO PERÍODO</b>", ParagraphStyle("r", parent=styleN, alignment=TA_CENTER)))
    res_rows = [["Código", "Descrição", "Un", "Qtd", "Valor Total"]]
    for (cod, desc, un), acc in sorted(resumo.items(), key=lambda x: x[0][0]):
        res_rows.append([cod, desc, un, f"{acc['qtd']:.2f}", format_brl(acc["val"])])
    res_tbl = Table(res_rows, colWidths=[0.14*W, 0.46*W, 0.07*W, 0.13*W, 0.20*W], repeatRows=1)
    res_tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.black),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),0.25,colors.black)]))
    story.append(res_tbl)
    story.append(Spacer(1, 6))

    total_val = sum(r["subtotal"] for r in linhas) if linhas else 0.0
    total_box = Table([[Paragraph("<b>Total:</b>", styleN), Paragraph(f"<b>{format_brl(total_val)}</b>", styleN)]], colWidths=[28*mm, 38*mm])
    total_box.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.75,colors.black),("ALIGN",(1,0),(1,0),"RIGHT")]))
    wrap = Table([[None, total_box]], colWidths=[doc.width - (28*mm+38*mm), (28*mm+38*mm)])
    story.append(wrap)

    doc.build(story, onFirstPage=lambda c,d:_on_page(c,d,titulo), onLaterPages=lambda c,d:_on_page(c,d,titulo))
    return buf.getvalue()

# ======================================================================
# AUX
# ======================================================================
def to_df(sess: Session, table) -> pd.DataFrame:
    rows = sess.execute(select(table)).scalars().all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows])

def gerar_numero_os(sess: Session) -> str:
    ano = datetime.now().year
    prefix = f"HAB-{ano}-"
    ultimo = sess.execute(select(OS).where(OS.numero.like(f"{prefix}%")).order_by(OS.id.desc())).scalars().first()
    seq = 1
    if ultimo:
        try:
            seq = int(str(ultimo.numero).split("-")[-1]) + 1
        except Exception:
            seq = (ultimo.id or 0) + 1
    return f"{prefix}{seq:04d}"

def obter_os_com_itens(sess: Session, os_id: int):
    os_row = sess.query(OS).options(selectinload(OS.itens).selectinload(OSItem.servico)).filter(OS.id == os_id).first()
    obra_row = sess.get(Obra, os_row.obra_id)
    itens = []
    for it in os_row.itens:
        sv = it.servico
        preco = it.preco_unit if it.preco_unit is not None else (sv.preco_unit or 0.0)
        itens.append({
            "codigo": sv.codigo,
            "descricao": sv.descricao,
            "unidade": sv.unidade,
            "qtd_prev": it.quantidade_prevista or 0.0,
            "preco_unit": preco,
            "subtotal": preco * (it.quantidade_prevista or 0.0),
        })
    return os_row, obra_row, itens

# ======================================================================
# PÁGINA CLIENTES
# ======================================================================
def page_clientes():
    st.markdown('<div class="section-title">Cadastro: Clientes</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)

    with SessionLocal() as sess:
        clientes = sess.query(Cliente).order_by(Cliente.nome.asc()).all()

    col_list, col_form = st.columns([1.2, 2.0])
    with col_list:
        ops = ["(Novo cliente)"] + [f"{c.id} — {c.nome}" for c in clientes]
        sel = st.selectbox("Selecione", ops, label_visibility="collapsed", key="cli_sel")

    with col_form:
        if sel != "(Novo cliente)":
            cli_id = int(sel.split("—", 1)[0].strip())
            with SessionLocal() as sess:
                cli = sess.get(Cliente, cli_id)

            st.text_input("Nome / Razão Social", cli.nome, key="cli_nome")
            st.text_input("CNPJ/CPF", cli.documento or "", key="cli_doc")
            st.text_area("Endereço", cli.endereco or "", key="cli_end", height=70)
            st.text_input("Contato", cli.contato or "", key="cli_contato")
            st.text_input("Email", cli.email or "", key="cli_email")
            st.text_input("Telefone", cli.telefone or "", key="cli_tel")
            st.checkbox("Ativo", value=(cli.ativo == 1), key="cli_ativo")

            st.button("Buscar pelo CNPJ", key="cli_busca_btn", on_click=cb_buscar_cnpj_cliente_edit)
            if st.session_state.get("cli_busca_msg_edit"):
                st.info(st.session_state["cli_busca_msg_edit"])

            if st.button("Salvar cliente", key="cli_save_btn"):
                with SessionLocal() as sess:
                    c = sess.get(Cliente, cli_id)
                    c.nome = st.session_state["cli_nome"]
                    c.documento = st.session_state["cli_doc"]
                    c.endereco = st.session_state["cli_end"]
                    c.contato = st.session_state["cli_contato"]
                    c.email = st.session_state["cli_email"]
                    c.telefone = st.session_state["cli_tel"]
                    c.ativo = 1 if st.session_state["cli_ativo"] else 0
                    sess.commit()
                flash("success", "Cliente atualizado.")
                _rerun()
        else:
            st.text_input("Nome / Razão Social", "", key="cli_nome_new")
            st.text_input("CNPJ/CPF", "", key="cli_doc_new")
            st.text_area("Endereço", "", key="cli_end_new", height=70)
            st.text_input("Contato", "", key="cli_cont_new")
            st.text_input("Email", "", key="cli_email_new")
            st.text_input("Telefone", "", key="cli_tel_new")

            st.button("Buscar pelo CNPJ", key="cli_busca_new", on_click=cb_buscar_cnpj_cliente_novo)
            if st.session_state.get("cli_busca_msg"):
                st.info(st.session_state["cli_busca_msg"])

            if st.button("Criar cliente", key="cli_create_btn"):
                with SessionLocal() as sess:
                    c = Cliente(
                        nome=st.session_state["cli_nome_new"],
                        documento=st.session_state["cli_doc_new"],
                        endereco=st.session_state["cli_end_new"],
                        contato=st.session_state["cli_cont_new"],
                        email=st.session_state["cli_email_new"],
                        telefone=st.session_state["cli_tel_new"],
                        ativo=1,
                    )
                    sess.add(c); sess.commit()
                flash("success", "Cliente criado.")
                _rerun()
    st.markdown('</div>', unsafe_allow_html=True)

# ======================================================================
# PÁGINA OBRAS
# ======================================================================
def page_obras():
    st.markdown('<div class="section-title">Cadastro: Obras</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)

    with SessionLocal() as sess:
        obras = sess.query(Obra).order_by(Obra.nome.asc()).all()
        clientes = sess.query(Cliente).order_by(Cliente.nome.asc()).all()
        servicos_all = sess.query(Servico).filter(Servico.ativo==1).order_by(Servico.descricao.asc()).all()

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
        if obra_edit is None:
            st.subheader("Nova obra")
            st.text_input("Nome da obra", "", key="obra_nome_new")
            st.text_input("CNPJ/CPF da obra", "", key="obra_doc_new")
            st.text_area("Endereço", "", key="obra_end_new", height=80)
            cli_ops = ["(sem cliente)"] + [c.nome for c in clientes]
            st.selectbox("Cliente", cli_ops, index=0, key="obra_cli_new")
            st.button("Buscar endereço pelo CNPJ", key="btn_buscar_cnpj_obra_new", on_click=cb_buscar_cnpj_obra_nova)
            if st.session_state.get("obra_busca_msg_new"):
                st.info(st.session_state["obra_busca_msg_new"])
            if st.button("Salvar nova obra", key="btn_nova_obra"):
                with SessionLocal() as sess:
                    nova = Obra(
                        nome=st.session_state["obra_nome_new"],
                        endereco=st.session_state["obra_end_new"],
                        documento=st.session_state["obra_doc_new"],
                        cliente=st.session_state["obra_cli_new"] if st.session_state["obra_cli_new"] != "(sem cliente)" else None,
                        ativo=1,
                    )
                    if st.session_state["obra_cli_new"] != "(sem cliente)":
                        cli_obj = sess.query(Cliente).filter(Cliente.nome==st.session_state["obra_cli_new"]).first()
                        if cli_obj:
                            nova.cliente_id = cli_obj.id
                    sess.add(nova); sess.commit()
                flash("success", "Obra criada.")
                _rerun()
        else:
            tab_dados, tab_anexos, tab_serv = st.tabs(["Dados", "Anexos", "Tabela de preços"])
            with tab_dados:
                nome_key = f"obra_nome_edit_{obra_edit.id}"
                doc_key  = f"obra_doc_edit_{obra_edit.id}"
                end_key  = f"obra_end_edit_{obra_edit.id}"
                st.text_input("Nome da obra", obra_edit.nome, key=nome_key)
                st.text_input("CNPJ/CPF da obra", obra_edit.documento or "", key=doc_key)
                st.text_area("Endereço", obra_edit.endereco or "", key=end_key, height=80)

                cli_ops = ["(sem cliente)"] + [c.nome for c in clientes]
                cli_default = 0
                if obra_edit.cliente_id:
                    for i,c in enumerate(clientes, start=1):
                        if c.id == obra_edit.cliente_id:
                            cli_default = i; break
                st.selectbox("Cliente", cli_ops, index=cli_default, key=f"obra_cli_edit_{obra_edit.id}")
                st.checkbox("Obra ativa", value=(obra_edit.ativo==1), key=f"obra_ativo_edit_{obra_edit.id}")
                st.checkbox("Obra bloqueada", value=(obra_edit.bloqueada==1), key=f"obra_bloq_edit_{obra_edit.id}")
                st.text_input("Motivo (se bloqueada)", obra_edit.bloqueada_motivo or "", key=f"obra_bloq_motivo_edit_{obra_edit.id}")

                st.button("Buscar endereço pelo CNPJ", key=f"btn_buscar_cnpj_obra_{obra_edit.id}", on_click=cb_buscar_cnpj_obra_edit, kwargs={"id_obra": obra_edit.id})
                if st.session_state.get(f"obra_busca_msg_edit_{obra_edit.id}"):
                    st.info(st.session_state[f"obra_busca_msg_edit_{obra_edit.id}"])

                if st.button("Salvar alterações", key=f"btn_save_obra_{obra_edit.id}"):
                    with SessionLocal() as sess:
                        ob = sess.get(Obra, obra_edit.id)
                        ob.nome = st.session_state[nome_key]
                        ob.documento = st.session_state[doc_key]
                        ob.endereco = st.session_state[end_key]
                        cli_sel = st.session_state[f"obra_cli_edit_{obra_edit.id}"]
                        if cli_sel != "(sem cliente)":
                            cli_obj = sess.query(Cliente).filter(Cliente.nome==cli_sel).first()
                            ob.cliente_id = cli_obj.id if cli_obj else None
                            ob.cliente = cli_obj.nome if cli_obj else None
                        else:
                            ob.cliente_id = None; ob.cliente=None
                        ob.ativo = 1 if st.session_state[f"obra_ativo_edit_{obra_edit.id}"] else 0
                        ob.bloqueada = 1 if st.session_state[f"obra_bloq_edit_{obra_edit.id}"] else 0
                        ob.bloqueada_motivo = st.session_state[f"obra_bloq_motivo_edit_{obra_edit.id}"]
                        if ob.bloqueada and not ob.bloqueada_desde:
                            ob.bloqueada_desde = date.today()
                        sess.commit()
                    flash("success", "Obra atualizada.")
                    _rerun()

            with tab_anexos:
                st.write("Envie documentos da obra para consulta futura:")
                up_prop = st.file_uploader("Proposta", key="up_prop")
                up_cont = st.file_uploader("Contrato", key="up_cont")
                up_cnpj = st.file_uploader("Cartão CNPJ", key="up_cnpj")
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
                st.write("Arquivos já enviados:")
                _download_btn_if_exists("Baixar proposta", obra_edit.anexo_proposta)
                _download_btn_if_exists("Baixar contrato", obra_edit.anexo_contrato)
                _download_btn_if_exists("Baixar CNPJ", obra_edit.anexo_cnpj)

            with tab_serv:
                st.markdown("### Tabela de preços desta obra (serviço x valor)")
                with SessionLocal() as sess:
                    obra_servs = sess.query(ObraServico).filter(ObraServico.obra_id==obra_edit.id, ObraServico.ativo==1).all()
                c1, c2, c3 = st.columns([2,1,1])
                with c1:
                    srv_ops = [f"{s.id} — {s.codigo} — {s.descricao}" for s in servicos_all]
                    srv_sel = st.selectbox("Serviço", srv_ops, key="obra_srv_sel")
                with c2:
                    preco_espec = st.number_input("Preço específico", min_value=0.0, value=0.0, step=1.0, format="%.2f", key="obra_srv_preco")
                with c3:
                    st.write("")
                    if st.button("Vincular/atualizar serviço", key="btn_vinc_srv"):
                        srv_id = int(srv_sel.split("—",1)[0].strip())
                        with SessionLocal() as sess:
                            osrv = sess.query(ObraServico).filter(ObraServico.obra_id==obra_edit.id, ObraServico.servico_id==srv_id).first()
                            if osrv:
                                osrv.preco_unit = preco_espec
                                osrv.ativo = 1
                            else:
                                sess.add(ObraServico(obra_id=obra_edit.id, servico_id=srv_id, preco_unit=preco_espec, ativo=1))
                            sess.commit()
                        flash("success", "Serviço vinculado/atualizado.")
                        _rerun()

                if obra_servs:
                    rows = []
                    for osrv in obra_servs:
                        serv_desc = next((f"{s.codigo} — {s.descricao}" for s in servicos_all if s.id == osrv.servico_id), str(osrv.servico_id))
                        rows.append({"Serviço": serv_desc, "Preço específico": osrv.preco_unit or 0.0})
                    st.dataframe(pd.DataFrame(rows), use_container_width=True)
                else:
                    st.info("Nenhum serviço específico vinculado.")
    st.markdown('</div>', unsafe_allow_html=True)

# ======================================================================
# PÁGINA SERVIÇOS
# ======================================================================
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
                flash("success", "Serviço atualizado.")
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
                flash("success", "Serviço criado.")
                _rerun()
    st.markdown('</div>', unsafe_allow_html=True)

# ======================================================================
# PÁGINA EMITIR OS (ajustada p/ manter OS)
# ======================================================================
def page_emitir_os():
    st.markdown('<div class="section-title">Emitir OS</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)

    with SessionLocal() as sess:
        obras = sess.query(Obra).filter(Obra.ativo == 1).order_by(Obra.nome.asc()).all()
        servicos = sess.query(Servico).filter(Servico.ativo == 1).order_by(Servico.descricao.asc()).all()
        os_list = sess.query(OS).order_by(OS.id.desc()).limit(50).all()

    # descobrir índice padrão do select de OS
    default_index = 0
    cur_id = st.session_state.get("current_os_id")
    if cur_id:
        for i, o in enumerate(os_list, start=1):  # +1 porque "(Nova OS)" é o 0
            if o.id == cur_id:
                default_index = i
                break

    ops = ["(Nova OS)"] + [f"{o.id} — {o.numero} — {o.status}" for o in os_list]
    os_sel = st.selectbox("Selecione OS", ops, index=default_index, key="os_sel_emitir")

    if os_sel == "(Nova OS)":
        os_db = None
        modo_novo = True
        st.session_state["current_os_id"] = None
    else:
        os_id = int(os_sel.split("—", 1)[0].strip())
        with SessionLocal() as sess:
            os_db = sess.get(OS, os_id)
        modo_novo = False
        st.session_state["current_os_id"] = os_db.id

    obra_opc = [f"{o.id} — {o.nome}" for o in obras]
    if modo_novo:
        obra_idx = 0
        os_status = "Aberta"
        os_obs = ""
        data_emissao = date.today()
    else:
        obra_idx = 0
        for i, o in enumerate(obras):
            if o.id == os_db.obra_id:
                obra_idx = i
                break
        os_status = os_db.status
        os_obs = os_db.observacoes or ""
        data_emissao = os_db.data_emissao or date.today()

    obra_sel = st.selectbox("Obra", obra_opc, index=obra_idx if obra_opc else 0)
    st.date_input("Data de emissão", value=data_emissao, key="emit_os_dt")
    st.selectbox("Status", STATUS_OPTIONS, index=STATUS_OPTIONS.index(os_status) if os_status in STATUS_OPTIONS else 0, key="emit_os_status")
    os_obs_new = st.text_area("Observações", os_obs, height=110)

    # -------- ITENS DA OS --------
    st.markdown("#### Itens da OS")
    col_s, col_q, col_p, col_btn = st.columns([2.8, 1, 1, 0.4])
    with col_s:
        srv_ops = [f"{s.id} — {s.codigo} — {s.descricao}" for s in servicos]
        srv_sel = st.selectbox("Serviço", srv_ops, key="emit_os_srv")
    with col_q:
        qtd = st.number_input("Qtd", min_value=0.0, value=1.0, step=1.0, format="%.2f")

    # determinar preço padrão a partir da obra
    obra_id_form = int(obra_sel.split("—", 1)[0].strip())
    srv_id_form = int(srv_sel.split("—", 1)[0].strip())
    preco_padrao_obra = 0.0
    with SessionLocal() as sess_tmp:
        ospec_tmp = (
            sess_tmp.query(ObraServico)
            .filter(ObraServico.obra_id == obra_id_form,
                    ObraServico.servico_id == srv_id_form,
                    ObraServico.ativo == 1)
            .first()
        )
        if ospec_tmp:
            preco_padrao_obra = ospec_tmp.preco_unit or 0.0
        else:
            sv_tmp = sess_tmp.get(Servico, srv_id_form)
            preco_padrao_obra = sv_tmp.preco_unit or 0.0

    with col_p:
        preco_in = st.number_input("Preço unit.", min_value=0.0,
                                   value=float(preco_padrao_obra),
                                   step=1.0, format="%.2f")
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
                    data_emissao=st.session_state["emit_os_dt"],
                    obra_id=obra_id,
                    status=st.session_state["emit_os_status"],
                    observacoes=os_obs_new,
                )
                sess.add(nova)
                sess.commit()
                st.session_state["current_os_id"] = nova.id  # <- guardar
            else:
                os_obj = sess.get(OS, os_db.id)
                os_obj.data_emissao = st.session_state["emit_os_dt"]
                os_obj.obra_id = obra_id
                os_obj.status = st.session_state["emit_os_status"]
                os_obj.observacoes = os_obs_new
                sess.commit()
                st.session_state["current_os_id"] = os_obj.id
        flash("success", "OS salva com sucesso.")
        _rerun()

    # adicionar item se for OS já existente
    if add_item and not modo_novo:
        srv_id = int(srv_sel.split("—", 1)[0].strip())
        obra_id = int(obra_sel.split("—", 1)[0].strip())
        with SessionLocal() as sess:
            os_obj = sess.get(OS, os_db.id)
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
        st.session_state["current_os_id"] = os_db.id  # garantir que fica na mesma OS
        flash("success", "Serviço incluído na OS.")
        _rerun()

    # mostrar itens da OS (tabela embaixo)
    if not modo_novo:
        with SessionLocal() as sess:
            os_row, obra_row, itens = obter_os_com_itens(sess, os_db.id)
        st.markdown("#### Serviços já adicionados a esta OS")
        if itens:
            df_it = pd.DataFrame(itens).rename(columns={
                "codigo":"Código",
                "descricao":"Descrição",
                "unidade":"Un",
                "qtd_prev":"Qtd",
                "preco_unit":"Preço unit.",
                "subtotal":"Subtotal",
            })
            total_os = df_it["Subtotal"].sum()
            st.dataframe(df_it, use_container_width=True)
            st.markdown(f"<div class='card'><b>Total da OS:</b> {format_brl(total_os)}</div>", unsafe_allow_html=True)
        else:
            st.info("Esta OS ainda não tem itens. Adicione usando o botão ➕.")
    else:
        st.info("Salve a OS primeiro para poder incluir e ver os serviços.")
    st.markdown('</div>', unsafe_allow_html=True)

# ======================================================================
# VISUALIZAR / IMPRIMIR
# ======================================================================
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
    df_view = df_view[(df_view["data_emissao"] >= ini) & (df_view["data_emissao"] <= fim)]
    q = st.text_input("Buscar por número da OS", "").strip()
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
        cli = sess.get(Cliente, obra_row.cliente_id) if obra_row.cliente_id else None

    st.write(f"**OS:** {os_row.numero}")
    st.write(f"**Data:** {os_row.data_emissao.strftime('%d/%m/%Y')}")
    st.write(f"**Obra:** {obra_row.nome}")
    st.write(f"**Endereço:** {obra_row.endereco}")
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

# ======================================================================
# MEDIÇÃO
# ======================================================================
def page_medicao():
    st.markdown('<div class="section-title">Medição Mensal</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)

    with SessionLocal() as sess:
        obras = sess.query(Obra).order_by(Obra.nome.asc()).all()
    if not obras:
        banner("info", "Cadastre obras primeiro.")
        st.markdown('</div>', unsafe_allow_html=True)
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

# ======================================================================
# RELATÓRIOS
# ======================================================================
def gerar_pdf_fechamento(cliente_nome: str, periodo_str: str, linhas: list[dict], logo_bytes: bytes | None, signature_bytes: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=28, bottomMargin=40, leftMargin=14, rightMargin=14)
    story = []
    story += _header_vertical_centralizado()
    info_tbl = Table([[Paragraph(f"<b>Cliente:</b> {cliente_nome}", styleSmall)],
                      [Paragraph(f"<b>Período:</b> {periodo_str}", styleSmall)]], colWidths=[doc.width])
    info_tbl.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.6,colors.black)]))
    story += [info_tbl, Spacer(1, 6)]
    titulo = "FECHAMENTO POR CLIENTE"
    tit_tbl = Table([[Paragraph(f"<b>{titulo}</b>", ParagraphStyle("t", parent=styleN, fontSize=11, alignment=TA_CENTER))]], colWidths=[doc.width])
    tit_tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")),

                                 ("BOX",(0,0),(-1,-1),0.5,colors.black)]))
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
    tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0), colors.black),
                             ("TEXTCOLOR",(0,0),(-1,0), colors.white),
                             ("GRID",(0,0),(-1,-1), 0.25, colors.black),
                             ("ALIGN",(3,1),(5,-1),"RIGHT")]))
    story.append(tbl); story.append(Spacer(1, 8))

    total_box = Table([[Paragraph("<b>Total geral:</b>", styleN), Paragraph(f"<b>{format_brl(total)}</b>", styleN)]], colWidths=[36*mm, 42*mm])
    total_box.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.75,colors.black),("ALIGN",(1,0),(1,0),"RIGHT")]))
    wrap = Table([[None, total_box]], colWidths=[doc.width-(36*mm+42*mm), (36*mm+42*mm)])
    story.append(wrap)

    doc.build(story, onFirstPage=lambda c,d:_on_page(c,d,titulo), onLaterPages=lambda c,d:_on_page(c,d,titulo))
    return buf.getvalue()

def page_relatorios():
    st.markdown('<div class="section-title">Relatórios</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    with SessionLocal() as sess:
        clientes = sess.query(Cliente).order_by(Cliente.nome.asc()).all()
    if not clientes:
        banner("info", "Cadastre clientes para emitir relatórios.")
        st.markdown('</div>', unsafe_allow_html=True)
        return
    cli_ops = [f"{c.id} — {c.nome}" for c in clientes]
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

# ======================================================================
# EXPORT
# ======================================================================
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
    st.markdown('<div class="section-title">Exportações</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)

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

    st.markdown('</div>', unsafe_allow_html=True)

# ======================================================================
# MENU / ROUTER
# ======================================================================
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

# ENTRYPOINT
main_router()
