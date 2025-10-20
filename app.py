# ================================
# PARTE 1 — Setup, Models, Utils
# ================================
import io, os, re, json, zipfile, calendar, tempfile, shutil
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Callable

import pandas as pd
import streamlit as st

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey,
    Text, func, select, and_
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session, selectinload

# ReportLab
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
)

# ================================
# Config Básico / Paths
# ================================
APP_NAME = "Sistema OS — Habisolute"
st.set_page_config(page_title=APP_NAME, page_icon="🧱", layout="wide")

BASE_DIR = Path(st.session_state.get("_app_dir", "."))  # fallback
DATA_DIR = (BASE_DIR / "data").resolve()
UPLOADS_DIR = (DATA_DIR / "uploads").resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "os_system.sqlite3"
ENGINE = create_engine(f"sqlite:///{DB_PATH}", echo=False, future=True)
SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, autocommit=False, expire_on_commit=False)
Base = declarative_base()

# ================================
# Autenticação / Permissões (simplificado)
# ================================
DEFAULT_PERMS = {
    "usuario": {"dashboard_view", "os_view"},
    "gestor":  {"dashboard_view", "os_view", "os_create", "relatorios_export"},
    "admin":   {"dashboard_view", "os_view", "os_create", "relatorios_export"},
}
if "auth" not in st.session_state:
    st.session_state["auth"] = {"username": "admin@local", "role": "admin", "is_admin": True}
s = st.session_state["auth"]

def has_perm(username: str, role: str, perm: str) -> bool:
    if st.session_state["auth"].get("is_admin"):  # superuser
        return True
    allowed = DEFAULT_PERMS.get(role or "usuario", set())
    return perm in allowed

def require_perm(perm: str) -> Callable:
    def deco(fn: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            if has_perm(s.get("username",""), s.get("role","usuario"), perm) or s.get("is_admin", False):
                return fn(*args, **kwargs)
            banner("error", f"Permissão necessária: {perm}")
        return wrapper
    return deco

# ================================
# Modelos
# ================================
class Cliente(Base):
    __tablename__ = "clientes"
    id = Column(Integer, primary_key=True)
    nome = Column(String, unique=True, nullable=False)
    documento = Column(String)           # CNPJ/CPF
    contato = Column(String)
    email = Column(String)
    telefone = Column(String)
    ativo = Column(Integer, default=1)

    bloqueado = Column(Integer, default=0)
    bloqueado_motivo = Column(String)
    bloqueado_desde = Column(Date)

    obras = relationship("Obra", back_populates="cliente_ref")

class Obra(Base):
    __tablename__ = "obras"
    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False)
    endereco = Column(String, nullable=False)
    cliente_id = Column(Integer, ForeignKey("clientes.id"))
    cliente = Column(String)  # legado / texto livre (fallback)
    ativo = Column(Integer, default=1)

    bloqueada = Column(Integer, default=0)
    bloqueada_motivo = Column(String)
    bloqueada_desde = Column(Date)

    anexo_cnpj = Column(String)
    anexo_proposta = Column(String)
    anexo_contrato = Column(String)

    cliente_ref = relationship("Cliente", back_populates="obras")
    os_list = relationship("OS", back_populates="obra")

class Servico(Base):
    __tablename__ = "servicos"
    id = Column(Integer, primary_key=True)
    codigo = Column(String, unique=True, nullable=False)
    descricao = Column(String, nullable=False)
    unidade = Column(String, nullable=False, default="un")
    preco_unit = Column(Float)  # catálogo (padrão)
    ativo = Column(Integer, default=1)

class ObraServico(Base):
    __tablename__ = "obra_servicos"
    id = Column(Integer, primary_key=True)
    obra_id = Column(Integer, ForeignKey("obras.id"), nullable=False)
    servico_id = Column(Integer, ForeignKey("servicos.id"), nullable=False)
    preco_unit = Column(Float)  # preço específico da obra
    ativo = Column(Integer, default=1)

class OS(Base):
    __tablename__ = "os"
    id = Column(Integer, primary_key=True)
    numero = Column(String, unique=True, nullable=False)
    data_emissao = Column(Date, nullable=False)
    obra_id = Column(Integer, ForeignKey("obras.id"), nullable=False)
    observacoes = Column(Text)
    status = Column(String, default="Aberta")

    obra = relationship("Obra", back_populates="os_list")
    itens = relationship("OSItem", back_populates="os", cascade="all, delete-orphan")

class OSItem(Base):
    __tablename__ = "os_itens"
    id = Column(Integer, primary_key=True)
    os_id = Column(Integer, ForeignKey("os.id"), nullable=False)
    servico_id = Column(Integer, ForeignKey("servicos.id"), nullable=False)
    quantidade_prevista = Column(Float)
    preco_unit = Column(Float)  # snapshot do preço na emissão

    os = relationship("OS", back_populates="itens")
    servico = relationship("Servico")

class Medicao(Base):
    __tablename__ = "medicoes"
    id = Column(Integer, primary_key=True)
    obra_id = Column(Integer, ForeignKey("obras.id"), nullable=False)
    numero = Column(Integer, nullable=False)
    periodo_ini = Column(Date, nullable=False)
    periodo_fim = Column(Date, nullable=False)
    criado_em = Column(Date, default=date.today)

Base.metadata.create_all(ENGINE)

# ================================
# UI helpers (banner/flash/CSS)
# ================================
CSS = """
<style>
.section-title { font-size:1.35rem; font-weight:700; margin: .25rem 0 1rem 0; }
.card { padding: .8rem 1rem; border: 1px solid #eee; border-radius: .75rem; background: #fff; margin-bottom: .6rem; }
.hb-side-title { display:flex; align-items:center; gap:.5rem; font-weight:700; }
.hb-dot { width:.6rem; height:.6rem; background:#FF7A00; border-radius:50%; display:inline-block; }
.flash { padding:.6rem .8rem; border-radius:.6rem; margin:.25rem 0; border:1px solid transparent; }
.flash.info { background:#f6f9fe; border-color:#dbe7fd; }
.flash.success { background:#f2fbf6; border-color:#cdeed6; }
.flash.warn { background:#fff8e6; border-color:#ffe6aa; }
.flash.error { background:#fff5f5; border-color:#ffcece; }
.flash b { font-weight:700; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

def banner(kind: str, msg: str):
    kind = kind if kind in {"info","success","warn","error"} else "info"
    st.markdown(f"<div class='flash {kind}'>{msg}</div>", unsafe_allow_html=True)

# Flash (fila de mensagens que sobrevivem ao rerun)
if "_flash" not in st.session_state:
    st.session_state["_flash"] = []

def flash(kind: str, msg: str):
    st.session_state["_flash"].append((kind, msg))

def flash_render(clear: bool = False):
    for k, m in st.session_state.get("_flash", []):
        banner(k, m)
    if clear:
        st.session_state["_flash"] = []

def _rerun():
    st.rerun()

# ================================
# Utilidades gerais
# ================================
STATUS_OPTIONS = ["Aberta", "Medido", "Faturado", "Cancelado"]

def to_df(sess: Session, model) -> pd.DataFrame:
    rows = sess.execute(select(model)).scalars().all()
    if not rows:
        return pd.DataFrame()
    recs = []
    cols = [c.name for c in model.__table__.columns]
    for r in rows:
        recs.append({c: getattr(r, c) for c in cols})
    return pd.DataFrame(recs)

def format_brl(v: float | int | None) -> str:
    try:
        v = float(v or 0.0)
    except Exception:
        v = 0.0
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

# Numeração de OS: HAB-YYYY-#### (sequência por ano)
def gerar_numero_os(sess: Session) -> str:
    ano = date.today().year
    prefix = f"HAB-{ano}-"
    ult = sess.execute(
        select(OS.numero).where(OS.numero.like(f"{prefix}%")).order_by(OS.id.desc())
    ).scalars().first()
    if ult:
        try:
            seq = int(ult.split("-")[-1]) + 1
        except Exception:
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"

# ================================
# Uploads e anexos
# ================================
def _abs_ok(rel: Optional[str]) -> tuple[bool, Optional[Path]]:
    if not rel: return False, None
    p = (UPLOADS_DIR / rel).resolve()
    try:
        ok = p.is_file() and UPLOADS_DIR in p.parents
    except Exception:
        ok = False
    return ok, (p if ok else None)

def _save_anexo(file, obra_id: int, tipo: str) -> Optional[str]:
    """Salva arquivo de anexo dentro de uploads/<obra_id>/<tipo>_<timestamp>.<ext> e retorna caminho relativo."""
    if not file: return None
    ext = Path(file.name).suffix.lower()
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    sub = Path(str(obra_id)) ; (UPLOADS_DIR / sub).mkdir(parents=True, exist_ok=True)
    fname = f"{tipo}_{ts}{ext}"
    dest = (UPLOADS_DIR / sub / fname).resolve()
    with open(dest, "wb") as f:
        f.write(file.read())
    rel = str(sub / fname)
    return rel

def _download_btn_if_exists(label: str, rel: Optional[str]):
    ok, path = _abs_ok(rel)
    if ok and path:
        with path.open("rb") as f:
            st.download_button(label, data=f.read(), file_name=path.name)

# ================================
# Integração CNPJ (stub)
# ================================
def fetch_cnpj_apibrasil(cnpj: str) -> Optional[dict]:
    """
    Stub simples: normaliza CNPJ e retorna None (sem chamada externa).
    Substitua por integração real quando possuir token.
    """
    dig = re.sub(r"\D+", "", cnpj or "")
    if len(dig) < 14:
        return None
    return {
        "razao": None,
        "fantasia": None,
        "email": None,
        "telefone": None,
        "endereco": None,
    }

# ================================
# Backup (DB + uploads)
# ================================
def make_full_backup() -> Path:
    tmp = Path(tempfile.gettempdir())
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = tmp / f"backup_os_{ts}.zip"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        if DB_PATH.exists():
            z.write(DB_PATH, arcname=f"db/{DB_PATH.name}")
        # uploads
        for root, _dirs, files in os.walk(UPLOADS_DIR):
            for fn in files:
                p = Path(root) / fn
                arc = p.relative_to(UPLOADS_DIR)
                z.write(p, arcname=f"uploads/{arc}")
    return out

# ================================
# Header / Branding (opcional)
# ================================
st.markdown(
    """
<div class="card" style="display:flex;align-items:center;gap:1rem;">
  <div style="width:.8rem;height:.8rem;background:#FF7A00;border-radius:50%"></div>
  <div><b>Sistema OS</b> — Habisolute Engenharia e Controle Tecnológico</div>
</div>
""",
    unsafe_allow_html=True,
)
# ================================
# PDFs (OS, Medição, Fechamento)
# ================================
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

        if st.button("🔎 Buscar CNPJ (APIBrasil)", use_container_width=True, key="cli_new_busca_cnpj"):
            if not (documento or "").strip():
                banner("warn", "Informe o CNPJ para buscar.")
            else:
                info = fetch_cnpj_apibrasil(documento)
                if not info:
                    banner("error", "Não foi possível obter dados deste CNPJ (verifique token, plano e o número informado).")
                else:
                    if not nome.strip():
                        st.session_state["cli_new_nome"] = info["razao"] or info["fantasia"] or ""
                    if not email.strip():
                        st.session_state["cli_new_email"] = info["email"] or ""
                    if not telefone.strip():
                        st.session_state["cli_new_tel"] = info["telefone"] or ""
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

                if st.button("🔄 Atualizar pelos dados do CNPJ (APIBrasil)", key=f"cli_edit_busca_cnpj_{c.id}"):
                    if not (c.documento or "").strip():
                        banner("warn", "Este cliente não possui CNPJ cadastrado.")
                    else:
                        info = fetch_cnpj_apibrasil(c.documento)
                        if not info:
                            banner("error", "Falha ao obter dados do CNPJ.")
                        else:
                            if not c.nome: c.nome = info["razao"] or info["fantasia"] or c.nome
                            if not c.email and info["email"]: c.email = info["email"]
                            if not c.telefone and info["telefone"]: c.telefone = info["telefone"]
                            sess.commit()
                            banner("success", "Cliente atualizado a partir do CNPJ.")

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

                ok_cnpj, _ = _abs_ok(o.anexo_cnpj)
                ok_prop, _ = _abs_ok(o.anexo_proposta)
                ok_cont, _ = _abs_ok(o.anexo_contrato)

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

                # ================== Serviços por Obra ==================
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
        desde = cli.bloqueado_desde.strftime("%d/%m/%Y") if cli and cli.bloqueado_desde else "-"
        banner("error", f"Cliente bloqueado desde {desde}. Motivo: {motivo}. Emissão desabilitada.")
    if obra_bloqueada:
        motivo_o = obra_sel.bloqueada_motivo or "Obra bloqueada."
        desde_o = obra_sel.bloqueada_desde.strftime("%d/%m/%Y") if obra_sel.bloqueada_desde else "-"
        banner("error", f"Obra bloqueada desde {desde_o}. Motivo: {motivo_o}. Emissão desabilitada.")
    bloqueio_ativo = (cliente_bloqueado or obra_bloqueada)

    data_emissao = st.date_input("Data de Emissão", value=date.today(), key="dt_emissao_os")
    observ = st.text_area("Observações (opcional)", key="obs_os")

    # Lista de serviços da OBRA com preço da obra
    with SessionLocal() as sess:
        servs_pairs = get_servicos_da_obra(sess, obra_sel.id)
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
                                        preco_unit=float(preco_snap)))
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
