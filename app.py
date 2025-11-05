# -*- coding: utf-8 -*-
# Habisolute — Sistema de OS (Streamlit)

import io, os, json, base64, zipfile, hashlib, hmac, secrets
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

import streamlit as st
import pandas as pd

# SQLAlchemy
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date, ForeignKey, Text, select, func
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session, selectinload

# ReportLab (PDF)
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, KeepTogether
)
from reportlab.lib.utils import ImageReader  # p/ desenhar assinatura dentro da célula

# =============================================================================
# Identidade / Config
# =============================================================================
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
        return pd.DataFrame(columns=["ts", "user", "level", "action", "meta", "system"])
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
    try: st.rerun()
    except Exception:
        try: st.experimental_rerun()
        except Exception:
            pass

# =============================================================================
# Auth (JSON local) — igual de antes
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
                if isinstance(data, dict) and isinstance(data.get("users"), dict):
                    fixed = _bootstrap_admin(data)
                    if fixed is not data:
                        _save_users(fixed)
                    return fixed
                if isinstance(data, dict):
                    fixed = _bootstrap_admin({"users": data})
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

def user_list() -> List[Dict[str, Any]]:
    db = _load_users()
    out = []
    for uname, rec in db.get("users", {}).items():
        r = dict(rec)
        r["username"] = uname
        out.append(r)
    out.sort(key=lambda r: (not r.get("is_admin", False), r["username"]))
    return out

# =============================================================================
# CSS — agora de novo com o degradê laranja na navegação
# =============================================================================
def _inject_css(theme: str | None = None):
    mode = (theme or st.session_state.get("theme_mode") or "Claro").lower()
    if mode == "claro":
        HB_BG, HB_CARD, HB_BORDER, HB_TEXT, HB_MUTED, HB_GLASS = (
            "#f3f4f6", "#ffffff", "#e2e8f0", "#0f172a", "#475569", "rgba(15,23,42,.02)"
        )
    else:
        HB_BG, HB_CARD, HB_BORDER, HB_TEXT, HB_MUTED, HB_GLASS = (
            "#0f1116", "#141821", "#2a3142", "#f8fafc", "#94a3b8", "rgba(255,255,255,.03)"
        )

    st.markdown(
        f"""
<style>
:root {{
  --hb-bg: {HB_BG};
  --hb-card: {HB_CARD};
  --hb-border: {HB_BORDER};
  --hb-text: {HB_TEXT};
  --hb-muted: {HB_MUTED};
  --hb-accent: {BRAND_COLOR};
  --hb-accent2: #ffb267;
}}
html, body, [data-testid="stAppViewContainer"] {{
  background: var(--hb-bg)!important;
  color: var(--hb-text)!important;
}}
/* <-- aqui volta o degradê laranja na barra lateral */
[data-testid="stSidebar"] {{
  background: linear-gradient(180deg, rgba(249,115,22,1) 0%, rgba(249,115,22,.75) 30%, rgba(15,17,22,.05) 100%) !important;
  border-right: 1px solid rgba(148,163,184,.25);
  backdrop-filter: blur(8px);
}}
[data-testid="stSidebar"] * {{
  color: #0f172a !important;
}}
.hb-side-title {{
  display:flex; align-items:center; gap:.5rem; margin:.25rem 0 1rem 0; font-weight:800;
}}
.hb-dot {{
  width:10px; height:10px; border-radius:999px;
  background: linear-gradient(90deg, var(--hb-accent), var(--hb-accent2));
  box-shadow:0 0 8px rgba(249,115,22,.6);
}}
[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label {{
  position:relative; display:flex; align-items:center; gap:.6rem;
  padding:.55rem .75rem; border-radius:14px;
  border:1px solid transparent;
  background: rgba(255,255,255,.25);
  transition:all .15s ease; margin:.15rem 0; cursor:pointer;
}}
[data-testid="stSidebar"] .stRadio input[type="radio"]{{opacity:0; position:absolute; left:-9999px;}}
[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label:hover{{
  background: rgba(255,255,255,.5);
}}
[data-testid="stSidebar"] .stRadio input[type="radio"]:checked + div{{
  background: linear-gradient(90deg, #fff 0%, #ffe0c7 100%);
  border:0!important; box-shadow:0 6px 26px rgba(249,115,22,.35);
  font-weight:800; border-radius:14px; padding:.55rem .75rem;
}}
.card{{
  background: linear-gradient(180deg, rgba(255,255,255,0.85), rgba(255,255,255,0.60));
  border:1px solid rgba(148,163,184,.40); border-radius:18px; padding:16px; margin-bottom:14px;
  box-shadow:0 6px 30px rgba(15,23,42,.08);
}}
.section-title{{
  background: linear-gradient(90deg, var(--hb-accent), var(--hb-accent2));
  color:#111; font-weight:800; text-align:center; padding:.6rem .8rem; border-radius:12px; margin:0 0 12px 0;
}}
.stButton>button, .stDownloadButton>button {{
  background: linear-gradient(180deg, var(--hb-accent), var(--hb-accent2));
  color:#111!important; font-weight:800; border:0; border-radius:12px; padding:.55rem 1rem;
}}
.hb-topbar {{ height:6px; background: linear-gradient(90deg, var(--hb-accent), var(--hb-accent2)); border-radius:6px; margin:4px 0 10px 0; }}
</style>
""",
        unsafe_allow_html=True,
    )

_inject_css()

# =============================================================================
# Banners / header
# =============================================================================
def banner(kind: str, text: str):
    icon = {"success":"✅","error":"⛔","warn":"⚠️","info":"ℹ️"}.get(kind,"ℹ️")
    st.markdown(
        f"""<div class="card" style="display:flex;gap:.6rem;align-items:center;border-left:5px solid #f97316;">
            <div>{icon}</div><div>{text}</div></div>""",
        unsafe_allow_html=True,
    )

def flash(kind: str, text: str):
    q = st.session_state.get("_flash", [])
    q.append({"k":kind,"t":text})
    st.session_state["_flash"] = q

def flash_render():
    q = st.session_state.get("_flash") or []
    for m in q:
        banner(m["k"], m["t"])
    st.session_state["_flash"] = []

def _render_header():
    st.markdown("<div class='hb-topbar'></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='card'><b>🏗️ {SYSTEM_NAME}</b></div>", unsafe_allow_html=True)

# =============================================================================
# Assinatura digital — helpers
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
# Login rápido (igual ao que você tinha)
# =============================================================================
def _recover_admin():
    db = _load_users()
    db = _bootstrap_admin(db)
    _save_users(db)
    flash("success", "Admin resetado para admin / 1234.")

def _auth_login_ui():
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.markdown("<div class='section-title'>🔐 Entrar</div>", unsafe_allow_html=True)
    c1,c2,c3 = st.columns([1.3,1.3,0.7])
    with c1:
        user = st.text_input("Usuário", key="login_user", label_visibility="collapsed", placeholder="Usuário")
    with c2:
        pwd = st.text_input("Senha", key="login_pass", label_visibility="collapsed", placeholder="Senha", type="password")
    with c3:
        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
        if st.button("Acessar", use_container_width=True):
            rec = user_get((user or "").strip())
            if not rec or not rec.get("active", True):
                flash("error", "Usuário inexistente ou inativo.")
            elif not _verify_password_simple(pwd, rec.get("password","")):
                flash("error", "Senha incorreta.")
            else:
                s["logged_in"] = True
                s["username"]  = (user or "").strip()
                s["is_admin"]  = bool(rec.get("is_admin", False))
                s["role"]      = rec.get("role", "usuario")
                s["must_change"]= bool(rec.get("must_change", False))
                prefs = load_user_prefs(); prefs["last_user"] = s["username"]; save_user_prefs(prefs)
                flash("success", f"Bem-vindo, {s['username']}!")
                _rerun()
    st.caption("Primeiro acesso: admin / 1234")
    if st.button("Recuperar acesso (admin)", use_container_width=True):
        _recover_admin(); _rerun()
    st.markdown("</div>", unsafe_allow_html=True)

if not s["logged_in"]:
    _auth_login_ui()
    flash_render()
    st.stop()

if s.get("must_change", False):
    st.warning("Troque a senha no módulo de usuários (simplificado).")
    # deixei assim só pra não travar
    s["must_change"] = False

# topo
_render_header()
nome_login = s.get("username") or load_user_prefs().get("last_user") or "—"
st.markdown(f"<div class='card'>👋 Olá, <b>{nome_login}</b> — Usuário</div>", unsafe_allow_html=True)

# toolbar (tema + sair)
c_t1, c_t2, c_t3 = st.columns([1,1,1])
with c_t1:
    s["theme_mode"] = st.radio("Tema", ["Claro","Escuro"], horizontal=True,
                               index=0 if s.get("theme_mode")=="Claro" else 1, key="theme_sel_main")
with c_t3:
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

# =============================================================================
# DB / modelos
# =============================================================================
Base = declarative_base()

class Cliente(Base):
    __tablename__ = "clientes"
    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False, unique=True)
    contato = Column(String)
    email = Column(String)
    telefone = Column(String)
    ativo = Column(Integer, default=1)
    obras = relationship("Obra", back_populates="cliente_ref")

class Obra(Base):
    __tablename__ = "obras"
    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False)
    endereco = Column(String, nullable=False)
    cliente_id = Column(Integer, ForeignKey("clientes.id"))
    ativo = Column(Integer, default=1)
    cliente_ref = relationship("Cliente", back_populates="obras")
    os_list = relationship("OS", back_populates="obra", cascade="all, delete")

class Servico(Base):
    __tablename__ = "servicos"
    id = Column(Integer, primary_key=True)
    codigo = Column(String, nullable=False, unique=True)
    descricao = Column(String, nullable=False)
    unidade = Column(String, nullable=False, default="un")
    preco_unit = Column(Float, default=0.0)
    itens = relationship("OSItem", back_populates="servico")

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
    quantidade_prevista = Column(Float, default=0.0)
    preco_unit = Column(Float, default=0.0)
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

STATUS_OPTIONS = ["Aberta","Em Execução","Medido em Aberto","Medido","Concluída","Cancelada"]

def format_brl(v: float) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"

# =============================================================================
# PDFs — assinatura agora fica na célula “Assinatura Laboratorista”
# =============================================================================
styles = getSampleStyleSheet()
styleN = styles["BodyText"]
styleSmall = ParagraphStyle("small", parent=styleN, fontSize=9, leading=11)
HB_ORANGE = colors.HexColor("#FF7A00")

def gerar_pdf_os(os_row, obra_row, itens: list[dict], show_prices: bool, signature_bytes: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=28, bottomMargin=36, leftMargin=14, rightMargin=14)
    story = []

    # cabeçalho simples
    story.append(Paragraph("<b>Habisolute Engenharia e Controle Tecnológico</b>", ParagraphStyle("h1", parent=styleN, fontSize=11, alignment=TA_CENTER)))
    story.append(Paragraph("ORDEM DE SERVIÇO", ParagraphStyle("h2", parent=styleN, fontSize=10, alignment=TA_CENTER)))
    story.append(Spacer(1,6))

    # dados
    story.append(Paragraph(f"<b>Nº:</b> {os_row.numero} — <b>Data:</b> {os_row.data_emissao.strftime('%d/%m/%Y')}", styleSmall))
    story.append(Paragraph(f"<b>Obra:</b> {obra_row.nome}", styleSmall))
    story.append(Paragraph(f"<b>Endereço:</b> {obra_row.endereco}", styleSmall))
    story.append(Paragraph(f"<b>Status:</b> {os_row.status}", styleSmall))
    story.append(Spacer(1,6))

    headers = ["Código","Descrição","Un","Qtd"]
    if show_prices:
        headers += ["Preço","Subtotal"]
    data_rows = [headers]
    total_val = 0.0
    for it in itens:
        row = [it["codigo"], it["descricao"], it["unidade"], f"{it['qtd_prev']:.2f}"]
        if show_prices:
            row += [format_brl(it["preco_unit"]), format_brl(it["subtotal"])]
            total_val += it["subtotal"]
        data_rows.append(row)

    W = doc.width
    col_widths = [0.16*W, 0.46*W, 0.08*W, 0.10*W, 0.10*W, 0.10*W] if show_prices else [0.20*W, 0.56*W, 0.10*W, 0.14*W]
    tbl = Table(data_rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0), colors.black),
        ("TEXTCOLOR",(0,0),(-1,0), colors.white),
        ("GRID",(0,0),(-1,-1), 0.25, colors.black),
        ("ALIGN",(0,0),(-1,-1), "LEFT"),
        ("ALIGN",(2,1),(3,-1),"CENTER"),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ]))
    story.append(tbl)

    if show_prices:
        story.append(Spacer(1,6))
        story.append(Paragraph(f"<b>Total:</b> {format_brl(total_val)}", styleSmall))

    story.append(Spacer(1,18))
    story.append(Paragraph("Data: ____/____/______", ParagraphStyle("dt", parent=styleN, fontSize=10, alignment=TA_CENTER)))
    story.append(Spacer(1,12))

    # assinatura — aqui vai a imagem na célula do laboratorista
    sig_img = None
    if signature_bytes:
        sig_img = Image(io.BytesIO(signature_bytes))
        sig_img.drawHeight = 12*mm
        sig_img.drawWidth  = 28*mm

    ass_data = [
        ["", "_______________________________", "", sig_img or "_______________________________", ""],
        ["", "Assinatura Cliente", "", "Assinatura Laboratorista", ""],
    ]
    ass_tbl = Table(ass_data, colWidths=[8*mm, 70*mm, 10*mm, 70*mm, 8*mm])
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

    def _on_page(canvas, doc):
        canvas.saveState()
        w, h = doc.pagesize
        canvas.setFillColor(HB_ORANGE)
        canvas.rect(0, h-10, w, 10, fill=1, stroke=0)
        canvas.setFillColor(colors.black)
        meta = f"Habisolute — {datetime.now().strftime('%d/%m/%Y %H:%M')} — pág. {canvas.getPageNumber()}"
        canvas.setFont("Helvetica", 8)
        canvas.drawCentredString(w/2, 16, meta)
        canvas.restoreState()

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return buf.getvalue()
# =============================================================================
# Helpers de consulta
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
        sess.execute(select(OS).where(OS.numero.like(f"{prefix}%")).order_by(OS.id.desc()))
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

# =============================================================================
# Backup
# =============================================================================
BACKUPS_DIR = BASE_DIR / "backups"; BACKUPS_DIR.mkdir(exist_ok=True, parents=True)

def make_full_backup() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = BACKUPS_DIR / f"backup_{ts}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if DB_PATH.exists():
            zf.write(DB_PATH, arcname=f"database/{DB_PATH.name}")
        anexos_root = BASE_DIR / "anexos"
        if anexos_root.exists():
            for p in anexos_root.rglob("*"):
                if p.is_file():
                    zf.write(p, arcname=str(p.relative_to(BASE_DIR)))
    return zip_path

# =============================================================================
# Excel com fallback (openpyxl -> xlsxwriter -> csv)
# =============================================================================
def make_os_excel_per_obras() -> tuple[bytes, str, str]:
    with SessionLocal() as sess:
        os_rows = (
            sess.query(OS)
            .options(selectinload(OS.obra))
            .order_by(OS.data_emissao.desc())
            .all()
        )
        data = []
        for r in os_rows:
            obra = sess.get(Obra, r.obra_id) if r.obra_id else None
            data.append({
                "Numero OS": r.numero,
                "Data Emissão": r.data_emissao.strftime("%d/%m/%Y") if r.data_emissao else "",
                "Status": r.status,
                "Obra": obra.nome if obra else "",
                "Endereço": obra.endereco if obra else "",
            })
        df = pd.DataFrame(data)

    # tenta openpyxl
    out = io.BytesIO()
    try:
        import openpyxl  # noqa
        with pd.ExcelWriter(out, engine="openpyxl", datetime_format="DD/MM/YYYY") as writer:
            df.to_excel(writer, index=False, sheet_name="OS")
        return out.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    except Exception:
        pass

    # tenta xlsxwriter
    out = io.BytesIO()
    try:
        import xlsxwriter  # noqa
        with pd.ExcelWriter(out, engine="xlsxwriter", datetime_format="dd/mm/yyyy") as writer:
            df.to_excel(writer, index=False, sheet_name="OS")
        return out.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    except Exception:
        pass

    # fallback CSV
    csv_bytes = df.to_csv(index=False, sep=";").encode("utf-8-sig")
    return csv_bytes, "text/csv", "csv"

# =============================================================================
# PÁGINAS DE CADASTRO (bem simples só para não dar NameError)
# =============================================================================
def page_clientes():
    st.markdown('<div class="section-title">Cadastro: Clientes</div>', unsafe_allow_html=True)
    with SessionLocal() as sess:
        df = to_df(sess, Cliente)
        nome = st.text_input("Nome do cliente")
        contato = st.text_input("Contato")
        email = st.text_input("Email")
        telefone = st.text_input("Telefone")
        if st.button("Salvar cliente"):
            if nome.strip():
                cli = Cliente(nome=nome.strip(), contato=contato, email=email, telefone=telefone)
                sess.add(cli); sess.commit()
                flash("success", "Cliente salvo.")
                _rerun()
        if not df.empty:
            st.dataframe(df, use_container_width=True)
        else:
            st.info("Nenhum cliente cadastrado.")

def page_obras():
    st.markdown('<div class="section-title">Cadastro: Obras</div>', unsafe_allow_html=True)
    with SessionLocal() as sess:
        clientes = sess.query(Cliente).order_by(Cliente.nome).all()
        cli_map = {c.nome: c.id for c in clientes}
        nome = st.text_input("Nome da obra")
        end = st.text_input("Endereço")
        cliente_nome = st.selectbox("Cliente", [""] + list(cli_map.keys()))
        if st.button("Salvar obra"):
            if nome.strip() and end.strip():
                obra = Obra(
                    nome=nome.strip(),
                    endereco=end.strip(),
                    cliente_id=cli_map.get(cliente_nome) if cliente_nome else None,
                )
                sess.add(obra); sess.commit()
                flash("success", "Obra salva.")
                _rerun()
        df = to_df(sess, Obra)
        if not df.empty:
            st.dataframe(df, use_container_width=True)
        else:
            st.info("Nenhuma obra cadastrada.")

def page_servicos():
    st.markdown('<div class="section-title">Cadastro: Serviços</div>', unsafe_allow_html=True)
    with SessionLocal() as sess:
        codigo = st.text_input("Código")
        descricao = st.text_input("Descrição")
        unidade = st.text_input("Unidade", value="un")
        preco = st.number_input("Preço unitário", min_value=0.0, step=1.0)
        if st.button("Salvar serviço"):
            if codigo.strip() and descricao.strip():
                sv = Servico(codigo=codigo.strip(), descricao=descricao.strip(), unidade=unidade.strip(), preco_unit=preco)
                sess.add(sv); sess.commit()
                flash("success", "Serviço salvo.")
                _rerun()
        df = to_df(sess, Servico)
        if not df.empty:
            st.dataframe(df, use_container_width=True)
        else:
            st.info("Nenhum serviço cadastrado.")

# =============================================================================
# Emitir OS
# =============================================================================
def page_emitir_os():
    st.markdown('<div class="section-title">Emitir OS</div>', unsafe_allow_html=True)
    with SessionLocal() as sess:
        obras = sess.query(Obra).order_by(Obra.nome).all()
        servicos = sess.query(Servico).order_by(Servico.descricao).all()
        obra_nomes = [f"{o.id} — {o.nome}" for o in obras]
        obra_sel = st.selectbox("Obra", obra_nomes) if obra_nomes else None
        status = st.selectbox("Status", STATUS_OPTIONS, index=0)
        obs = st.text_area("Observações")
        st.markdown("**Itens da OS**")
        if "itens_os_tmp" not in st.session_state:
            st.session_state["itens_os_tmp"] = []
        c1,c2,c3,c4 = st.columns([2,1,1,1])
        with c1:
            sv_sel = st.selectbox("Serviço", [f"{s.id} — {s.codigo} — {s.descricao}" for s in servicos]) if servicos else None
        with c2:
            qtd = st.number_input("Qtd", min_value=0.0, value=1.0, step=1.0)
        with c3:
            preco_custom = st.number_input("Preço unit.", min_value=0.0, value=0.0, step=1.0)
        with c4:
            if st.button("Adicionar item"):
                if sv_sel:
                    sv_id = int(sv_sel.split(" — ")[0])
                    sv = sess.get(Servico, sv_id)
                    st.session_state["itens_os_tmp"].append({
                        "servico_id": sv.id,
                        "codigo": sv.codigo,
                        "descricao": sv.descricao,
                        "unidade": sv.unidade,
                        "qtd": qtd,
                        "preco": preco_custom if preco_custom > 0 else (sv.preco_unit or 0.0),
                    })
        if st.session_state["itens_os_tmp"]:
            df_tmp = pd.DataFrame(st.session_state["itens_os_tmp"])
            df_tmp["subtotal"] = df_tmp["qtd"] * df_tmp["preco"]
            st.dataframe(df_tmp, use_container_width=True)
        if st.button("Salvar OS", use_container_width=True):
            if not obra_sel:
                flash("error", "Selecione uma obra.")
                _rerun()
            obra_id = int(obra_sel.split(" — ")[0])
            num_os = gerar_numero_os(sess)
            os_obj = OS(
                numero=num_os,
                data_emissao=date.today(),
                obra_id=obra_id,
                status=status,
                observacoes=obs,
            )
            sess.add(os_obj); sess.flush()
            for it in st.session_state["itens_os_tmp"]:
                item = OSItem(
                    os_id=os_obj.id,
                    servico_id=it["servico_id"],
                    quantidade_prevista=it["qtd"],
                    preco_unit=it["preco"],
                )
                sess.add(item)
            sess.commit()
            st.session_state["itens_os_tmp"] = []
            flash("success", f"OS {num_os} emitida.")
            _rerun()

# =============================================================================
# Função para pegar OS com itens (para visualizar e PDF)
# =============================================================================
def obter_os_com_itens(sess: Session, os_id: int):
    os_row = (
        sess.query(OS)
        .options(selectinload(OS.itens).selectinload(OSItem.servico))
        .filter(OS.id == os_id)
        .first()
    )
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

# =============================================================================
# Visualizar / Imprimir
# =============================================================================
def page_visualizar_imprimir():
    st.markdown('<div class="section-title">Visualizar / Imprimir</div>', unsafe_allow_html=True)
    with SessionLocal() as sess:
        df_os = to_df(sess, OS)
        if df_os.empty:
            st.info("Nenhuma OS cadastrada.")
            return
        obras = {o.id: o for o in sess.query(Obra).all()}
    df_os["data_emissao"] = pd.to_datetime(df_os["data_emissao"]).dt.date
    df_os["obra_nome"] = df_os["obra_id"].map(lambda oid: obras[oid].nome if oid in obras else "")
    df_os = df_os.sort_values("data_emissao", ascending=False).reset_index(drop=True)

    sel = st.selectbox("Selecione a OS", df_os["numero"] + " — " + df_os["obra_nome"])
    num_sel = sel.split(" — ")[0]
    with SessionLocal() as sess:
        os_row = sess.query(OS).filter(OS.numero == num_sel).first()
        os_row, obra_row, itens = obter_os_com_itens(sess, os_row.id)

    st.write(f"**OS:** {os_row.numero}")
    st.write(f"**Obra:** {obra_row.nome}")
    st.write(f"**Endereço:** {obra_row.endereco}")
    st.write(f"**Status:** {os_row.status}")

    if itens:
        df_it = pd.DataFrame(itens)
        df_it = df_it.rename(columns={
            "codigo":"Código","descricao":"Descrição","unidade":"Un","qtd_prev":"Qtd","preco_unit":"Preço","subtotal":"Subtotal"
        })
        st.dataframe(df_it, use_container_width=True)
    else:
        st.info("OS sem itens.")

    sig_bytes = load_signature_bytes()
    pdf_interno = gerar_pdf_os(os_row, obra_row, itens, show_prices=True, signature_bytes=sig_bytes)
    pdf_cliente = gerar_pdf_os(os_row, obra_row, itens, show_prices=False, signature_bytes=sig_bytes)

    c1,c2 = st.columns(2)
    with c1:
        st.download_button("Baixar PDF (interno)", data=pdf_interno, file_name=f"{os_row.numero}_interno.pdf", mime="application/pdf")
    with c2:
        st.download_button("Baixar PDF (cliente)", data=pdf_cliente, file_name=f"{os_row.numero}_cliente.pdf", mime="application/pdf")

# =============================================================================
# Medição (mínima) — usa assinatura
# =============================================================================
def page_medicao():
    st.markdown('<div class="section-title">Medição Mensal</div>', unsafe_allow_html=True)
    with SessionLocal() as sess:
        obras = sess.query(Obra).order_by(Obra.nome).all()
        os_list = sess.query(OS).order_by(OS.data_emissao.desc()).all()
    if not obras or not os_list:
        st.info("Cadastre obras e OS para gerar medição.")
        return
    obra_sel = st.selectbox("Obra", [f"{o.id} — {o.nome}" for o in obras])
    obra_id = int(obra_sel.split(" — ")[0])
    periodo = st.date_input("Período", value=(date.today().replace(day=1), date.today()))
    ini, fim = (periodo if isinstance(periodo, (list,tuple)) else (periodo, periodo))
    # filtra OS da obra
    with SessionLocal() as sess:
        os_obra = (
            sess.query(OS)
            .filter(OS.obra_id == obra_id)
            .filter(OS.data_emissao >= ini)
            .filter(OS.data_emissao <= fim)
            .order_by(OS.data_emissao)
            .all()
        )
        linhas = []
        for osr in os_obra:
            osr, obra_row, itens = obter_os_com_itens(sess, osr.id)
            for it in itens:
                linhas.append({
                    "data": osr.data_emissao,
                    "os_num": osr.numero,
                    "codigo": it["codigo"],
                    "descricao": it["descricao"],
                    "un": it["unidade"],
                    "qtd": it["qtd_prev"],
                    "preco": it["preco_unit"],
                    "subtotal": it["subtotal"],
                })
    if not linhas:
        st.info("Nenhum item encontrado no período.")
        return
    df_lin = pd.DataFrame(linhas)
    st.dataframe(df_lin, use_container_width=True)

    if st.button("Baixar PDF da medição"):
        from datetime import datetime as _dt
        sig = load_signature_bytes()
        # reutiliza função de OS para montar rápido
        pdf_med = gerar_pdf_os(  # uso a mesma estrutura só pra simplificar
            os_row=type("X", (), {"numero": f"MED-{obra_id}", "data_emissao": ini, "status": "Medição"})(),
            obra_row=type("Y", (), {"nome": obra_sel, "endereco": "-"})(),
            itens=[{
                "codigo": r["codigo"],
                "descricao": r["descricao"],
                "unidade": r["un"],
                "qtd_prev": r["qtd"],
                "preco_unit": r["preco"],
                "subtotal": r["subtotal"],
            } for r in linhas],
            show_prices=True,
            signature_bytes=sig,
        )
        st.download_button(
            "Download agora",
            data=pdf_med,
            file_name=f"medicao_{obra_id}_{_dt.now().strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
            key="dl_med_pdf",
        )

# =============================================================================
# Relatórios (mínimo)
# =============================================================================
def page_relatorios():
    st.markdown('<div class="section-title">Relatórios</div>', unsafe_allow_html=True)
    with SessionLocal() as sess:
        df = to_df(sess, OS)
    if df.empty:
        st.info("Nenhuma OS para relatar.")
        return
    st.dataframe(df, use_container_width=True)
# =============================================================================
# Exportações
# =============================================================================
def page_export():
    st.markdown('<div class="section-title">Exportações</div>', unsafe_allow_html=True)

    with st.expander("Backup (DB + anexos)", expanded=False):
        if st.button("Gerar backup ZIP"):
            p = make_full_backup()
            st.download_button(
                "Baixar backup",
                data=p.read_bytes(),
                file_name=p.name,
                mime="application/zip",
            )

    with st.expander("Exportar OS por obras (Excel/CSV)", expanded=True):
        bytes_x, mime_x, ext_x = make_os_excel_per_obras()
        st.download_button(
            f"Baixar OS ({ext_x.upper()})",
            data=bytes_x,
            file_name=f"os_por_obras.{ext_x}",
            mime=mime_x,
        )
        st.caption("Se o servidor não tiver openpyxl/xlsxwriter, ele baixa CSV automaticamente 😉")

    with st.expander("Assinatura digital (PDF)", expanded=True):
        st.write("Envie uma imagem de assinatura (PNG/JPG). Ela vai sair no campo “Assinatura Laboratorista”.")
        up = st.file_uploader("Imagem da assinatura", type=["png","jpg","jpeg"])
        if up is not None:
            if save_signature_file(up):
                flash("success", "Assinatura salva! Gere um PDF para testar.")
        sig = load_signature_bytes()
        if sig:
            st.image(sig, width=180, caption="Assinatura atual")

# =============================================================================
# MENU / ROUTER
# =============================================================================
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
page = st.sidebar.radio("Ir para", MENU, index=0, label_visibility="collapsed")

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

# entry
main_router()
