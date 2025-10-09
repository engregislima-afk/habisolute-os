# app.py — Habisolute OS (layout laranja/preto + PDFs conforme anexo)
from __future__ import annotations
import io, calendar
from pathlib import Path
from datetime import date, datetime

import streamlit as st
import pandas as pd

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date, ForeignKey, Text,
    select, func
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session, selectinload

# ================= Streamlit UI / Tema (LARANJA FORTE + PRETO) =================
st.set_page_config(page_title="Habisolute — OS", layout="wide", page_icon="📅")

st.markdown("""
<style>
:root {
  --hb-bg:#0f1116;
  --hb-card:#141821;
  --hb-card-border:#2a3142;
  --hb-text:#f5f5f5;
  --hb-muted:#c7cfdb;
  --hb-accent:#ff4d00; /* laranja forte */
  --hb-accent-2:#ff9b42;
}
html, body, [data-testid="stAppViewContainer"]{
  background:var(--hb-bg)!important;color:var(--hb-text)!important;
}
.card{
  background:var(--hb-card);border:1px solid var(--hb-card-border);
  padding:1rem;border-radius:14px;margin-bottom:1rem;
}
/* Títulos das seções em faixa LARANJA */
.card h3, .section-title{
  background:var(--hb-accent);
  color:#111!important;
  font-weight:800;
  text-align:center;
  padding:.6rem 0;
  border-radius:10px;
  margin:-.25rem 0 1rem 0;
}
h1,h2,h3,h4,h5{color:var(--hb-text)!important;}
.stTextInput input,.stTextArea textarea,.stNumberInput input,.stDateInput input{
  color:#fff!important;background:#0b0e14!important;border:1px solid #2b3548!important;
}
div[data-baseweb="select"] input,div[data-baseweb="select"] span{color:#fff!important;}
label,.stMarkdown p,.block-label{color:var(--hb-text)!important;}
.stButton>button,.stDownloadButton>button{
  background:linear-gradient(180deg,var(--hb-accent),var(--hb-accent-2));
  color:#111!important;font-weight:700;border:0;border-radius:10px;
}
.stAlert{border-left:6px solid var(--hb-accent)!important;}
.metric-container{
  background:#10131a;padding:.75rem;border-radius:12px;border:1px solid var(--hb-card-border);
}
.dataframe thead tr th{
  background:#1b2230!important;color:#fff!important;border-bottom:1px solid var(--hb-card-border)!important;
}
.hr{height:1px;background:#2b3244;margin:1rem 0;}
</style>
""", unsafe_allow_html=True)
st.markdown("""
<style>
/* Título da sidebar em laranja da marca */
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
  color: var(--hb-accent) !important;  /* #FF7A00 */
  opacity: 1 !important;
}
</style>
""", unsafe_allow_html=True)

# ================= Database =================
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
    cliente = Column(String)  # legado
    cliente_id = Column(Integer, ForeignKey("clientes.id"))
    ativo = Column(Integer, default=1)
    bloqueada = Column(Integer, default=0)
    bloqueada_motivo = Column(Text)
    bloqueada_desde = Column(Date)

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

class OS(Base):
    __tablename__ = "os"
    id = Column(Integer, primary_key=True)
    numero = Column(String, nullable=False, unique=True)  # HAB-AAAA-####
    data_emissao = Column(Date, default=date.today)
    obra_id = Column(Integer, ForeignKey("obras.id"))
    status = Column(String, default="Aberta")  # Aberta, Em Execução, Medido em Aberto, Medido, Concluída, Cancelada
    observacoes = Column(Text)

    obra = relationship("Obra", back_populates="os_list")
    itens = relationship("OSItem", back_populates="os", cascade="all, delete")

class OSItem(Base):
    __tablename__ = "os_itens"
    id = Column(Integer, primary_key=True)
    os_id = Column(Integer, ForeignKey("os.id"))
    servico_id = Column(Integer, ForeignKey("servicos.id"))
    quantidade_prevista = Column(Float)
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

# -------- PRAGMAs e índices (robustez/performance) --------
with engine.begin() as conn:
    conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
    conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_os_obra_data ON os(obra_id, data_emissao);")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_os_status ON os(status);")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_os_numero ON os(numero);")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_ositem_osid ON os_itens(os_id);")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_medicoes_obra ON medicoes(obra_id);")

# -------- migrações leves --------
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
        if "cliente_id" not in cols:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN cliente_id INTEGER")
        if "bloqueada" not in cols:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN bloqueada INTEGER DEFAULT 0")
        if "bloqueada_motivo" not in cols:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN bloqueada_motivo TEXT")
        if "bloqueada_desde" not in cols:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN bloqueada_desde DATE")
        # backfill legado -> cliente_id
        obras = conn.exec_driver_sql(
            "SELECT id, cliente FROM obras WHERE cliente IS NOT NULL AND TRIM(cliente)<>'' AND cliente_id IS NULL"
        ).fetchall()
        for oid, nome in obras:
            nm = (nome or "").strip()
            if not nm: continue
            row = conn.exec_driver_sql("SELECT id FROM clientes WHERE nome = ?", (nm,)).fetchone()
            if row is None:
                conn.exec_driver_sql("INSERT INTO clientes (nome, ativo) VALUES (?,1)", (nm,))
                row = conn.exec_driver_sql("SELECT id FROM clientes WHERE nome = ?", (nm,)).fetchone()
            conn.exec_driver_sql("UPDATE obras SET cliente_id=? WHERE id=?", (row[0], oid))

_ensure_medicoes_schema(engine)
_ensure_clientes_schema_and_backfill(engine)

# ================= Helpers =================
STATUS_OPTIONS = ["Aberta", "Em Execução", "Medido em Aberto", "Medido", "Concluída", "Cancelada"]

def to_df(sess: Session, table) -> pd.DataFrame:
    rows = sess.execute(select(table)).scalars().all()
    if not rows:
        return pd.DataFrame()
    recs = []
    for r in rows:
        d = {c.name: getattr(r, c.name) for c in r.__table__.columns}
        recs.append(d)
    return pd.DataFrame(recs)

def gerar_numero_os(sess: Session) -> str:
    ano = datetime.now().year
    prefix = f"HAB-{ano}-"
    ultimo = sess.execute(
        select(OS).where(OS.numero.like(f"{prefix}%")).order_by(OS.id.desc())
    ).scalars().first()
    if not ultimo:
        seq = 1
    else:
        try:
            seq = int(ultimo.numero.split("-")[-1]) + 1
        except:
            seq = ultimo.id + 1
    return f"{prefix}{seq:04d}"

def format_brl(v: float) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "R$ 0,00"

# ================= PDF (padrão exato do seu anexo) =================
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
# Imports do ReportLab (KeepTogether com fallback)
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
try:
    from reportlab.platypus import KeepTogether
except Exception:
    from reportlab.platypus.flowables import KeepTogether

styles = getSampleStyleSheet()
styleN = styles["BodyText"]
styleSmall = ParagraphStyle("small", parent=styleN, fontSize=9, leading=11)
styleTiny  = ParagraphStyle("tiny",  parent=styleN, fontSize=8, leading=10)

HB_ORANGE = colors.HexColor("#FF7A00")  # laranja forte
FORM_CODE = "FORM.H.012.00"

def _header_vertical_centralizado() -> list:
    """4 linhas centralizadas: Nome / e-mail / telefone / FORM."""
    p1 = Paragraph("<b>Habisolute Engenharia e Controle Tecnológico</b>", ParagraphStyle(
        "hdr1", parent=styleN, fontSize=11, leading=13, alignment=TA_CENTER))
    p2 = Paragraph("contato@habisoluteengenharia.com.br", ParagraphStyle(
        "hdr2", parent=styleN, fontSize=9, leading=11, alignment=TA_CENTER))
    p3 = Paragraph("(16) 3877-9480", ParagraphStyle(
        "hdr3", parent=styleN, fontSize=9, leading=11, alignment=TA_CENTER))
    p4 = Paragraph(FORM_CODE, ParagraphStyle(
        "hdr4", parent=styleN, fontSize=9, leading=11, alignment=TA_CENTER))

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

def _on_page(canvas, doc, _titulo_meta: str):
    """Faixa laranja no topo; rodapé com linha laranja e meta-linha centralizada."""
    from reportlab.lib.colors import black
    canvas.saveState()
    w, h = doc.pagesize

    # === Faixa laranja forte no topo ===
    canvas.setFillColor(HB_ORANGE)
    canvas.setStrokeColor(HB_ORANGE)
    canvas.rect(0, h-10, w, 10, fill=1, stroke=0)  # 10pt de altura

    # === Rodapé: linha laranja e meta-linha logo abaixo ===
    footer_y = 18  # base do rodapé
    canvas.setFillColor(HB_ORANGE)
    canvas.setStrokeColor(HB_ORANGE)
    canvas.rect(0, footer_y + 10, w, 2, fill=1, stroke=0)  # linha laranja (2pt)

    # meta-linha centralizada
    from datetime import datetime as _dt
    pagina = canvas.getPageNumber()
    agora = _dt.now().strftime("%d/%m/%Y %H:%M")
    meta_txt = f"Habisolute Engenharia e Controle Tecnológico — {FORM_CODE}  {agora}  pág. {pagina}"

    canvas.setFont("Helvetica", 8.5)
    canvas.setFillColor(black)
    text_width = canvas.stringWidth(meta_txt, "Helvetica", 8.5)
    canvas.drawString((w - text_width)/2.0, footer_y, meta_txt)

    canvas.restoreState()

def _logo_img_story(logo_bytes: bytes | None, max_w: float, max_h: float):
    if not logo_bytes:
        return None
    try:
        img = Image(io.BytesIO(logo_bytes))
        iw, ih = img.wrap(0, 0)
        scale = min(max_w / iw, max_h / ih)
        img.drawWidth = iw * scale
        img.drawHeight = ih * scale
        return img
    except:
        return None

def gerar_pdf_os(os_row, obra_row, itens: list[dict], show_prices: bool, logo_bytes: bytes | None) -> bytes:
    """
    ORDEM DE SERVIÇO — Layout:
      - (_on_page) faixa laranja no topo + rodapé laranja com meta-linha
      - Cabeçalho vertical centralizado (4 linhas)
      - Bloco 'cadastro da obra' (Status/Obra/Endereço/Cliente) logo após o FORM
      - Faixa cinza com 'ORDEM DE SERVIÇO Nº ...  DATA: ...'
      - Tabela com cabeçalho preto ocupando 100% da largura útil e linha-rodapé de TOTAL (quando show_prices)
      - Data (em branco) e assinaturas posicionadas um pouco mais abaixo
    """
    buf = io.BytesIO()
    # Margens: laterais menores, mas com margem em toda a folha
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=28, bottomMargin=36,
        leftMargin=14, rightMargin=14
    )
    story = []

    # 1) Cabeçalho vertical centralizado (4 linhas)
    story += _header_vertical_centralizado()

    # 2) Cadastro da obra (logo após o FORM)
    with SessionLocal() as s:
        cli = s.get(Cliente, obra_row.cliente_id) if obra_row.cliente_id else None
    info_tbl = Table([
        [Paragraph(f"<b>Status:</b> {os_row.status}", styleSmall)],
        [Paragraph(f"<b>Obra:</b> {obra_row.nome}", styleSmall)],
        [Paragraph(f"<b>Endereço:</b> {obra_row.endereco}", styleSmall)],
        [Paragraph(f"<b>Cliente:</b> {cli.nome if cli else (obra_row.cliente or '-')}", styleSmall)],
    ], colWidths=[doc.width])
    info_tbl.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),0),
        ("BOTTOMPADDING",(0,0),(-1,-1),2),
        ("LEFTPADDING",(0,0),(-1,-1),0),
        ("RIGHTPADDING",(0,0),(-1,-1),0),
    ]))
    story += [info_tbl, Spacer(1, 6)]

    # 3) Faixa cinza com título da OS
    titulo_os = f"ORDEM DE SERVIÇO Nº {os_row.numero}    DATA: {os_row.data_emissao.strftime('%d/%m/%Y')}"
    tit_tbl = Table([[Paragraph(f"<b>{titulo_os}</b>", ParagraphStyle(
        'titOS', parent=styleN, fontSize=11, leading=13, alignment=TA_CENTER))]],
        colWidths=[doc.width]
    )
    tit_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")),
        ("TEXTCOLOR",(0,0),(-1,-1), colors.black),
        ("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),6),
        ("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LEFTPADDING",(0,0),(-1,-1),8),
        ("RIGHTPADDING",(0,0),(-1,-1),8),
    ]))
    story += [tit_tbl, Spacer(1, 8)]

    # 4) Tabela de itens (cabeçalho preto) + linha-rodapé TOTAL
    headers = ["Código", "Descrição", "Un", "Qtd"]
    if show_prices:
        headers += ["Preço Unit", "Sub Total"]

    data_rows = [headers]
    for it in itens:
        row = [it["codigo"], it["descricao"], it["unidade"], f"{it['qtd_prev']:.2f}"]
        if show_prices:
            row += [format_brl(it["preco_unit"]), format_brl(it["subtotal"])]
        data_rows.append(row)

    # LARGURAS DINÂMICAS: usam 100% da largura útil
    W = doc.width
    if show_prices:
        # Código 16% | Descrição 44% | Un 6% | Qtd 10% | Preço 12% | Sub Total 12%
        col_widths = [0.16*W, 0.44*W, 0.06*W, 0.10*W, 0.12*W, 0.12*W]
    else:
        # Sem preços: Código 18% | Descrição 56% | Un 8% | Qtd 18%
        col_widths = [0.18*W, 0.56*W, 0.08*W, 0.18*W]

    total_val = sum(it["subtotal"] for it in itens) if (show_prices and itens) else 0.0
    total_row_index = None
    if show_prices:
        fillers = [""] * (len(headers) - 2)
        data_rows.append(fillers + ["Total:", format_brl(total_val)])
        total_row_index = len(data_rows) - 1

    tbl = Table(data_rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        # Cabeçalho preto
        ("BACKGROUND", (0,0), (-1,0), colors.black),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),

        # Corpo (grade)
        ("GRID", (0,0), (-1,-1), 0.25, colors.black),
        ("LEFTPADDING",(0,0),(-1,-1),6),
        ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3),
        ("BOTTOMPADDING",(0,0),(-1,-1),3),

        # Alinhamentos
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

    # 5) Espaço maior antes de Data/Assinaturas
    story += [Spacer(1, 24)]
    story.append(Paragraph("Data: ____/____/______", ParagraphStyle(
        "dt", parent=styleN, fontSize=10, alignment=TA_CENTER)))

    story.append(Spacer(1, 22))
    ass_tbl = Table(
        [
            ["", "_______________________________", "", "_______________________________", ""],
            ["", "Assinatura Cliente", "", "Assinatura Laboratorista", ""],
        ],
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

    # Build
    doc.build(
        story,
        onFirstPage=lambda c,d:_on_page(c,d,""),
        onLaterPages=lambda c,d:_on_page(c,d,"")
    )
    return buf.getvalue()

def gerar_pdf_medicao(obra_nome: str, periodo_str: str, linhas: list[dict], logo_bytes: bytes | None, medicao_num: int) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        topMargin=28, bottomMargin=36, leftMargin=14, rightMargin=14
    )
    story = []

    # Cabeçalho vertical
    story += _header_vertical_centralizado()

    # Bloco de informações
    info_tbl = Table([
        [Paragraph(f"<b>Obra:</b> {obra_nome}", styleSmall)],
        [Paragraph(f"<b>Período:</b> {periodo_str}", styleSmall)],
    ], colWidths=[doc.width])
    info_tbl.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),2),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
    ]))
    story += [info_tbl, Spacer(1, 6)]

    # Faixa cinza do título
    titulo = f"RELATÓRIO DE MEDIÇÃO — Medição nº {medicao_num}"
    tit_tbl = Table([[Paragraph(f"<b>{titulo}</b>", ParagraphStyle(
        "titMED", parent=styleN, fontSize=11, leading=13, alignment=TA_CENTER))]],
        colWidths=[doc.width]
    )
    tit_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")),
        ("TEXTCOLOR",(0,0),(-1,-1), colors.black),
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LEFTPADDING",(0,0),(-1,-1),8), ("RIGHTPADDING",(0,0),(-1,-1),8),
    ]))
    story += [tit_tbl, Spacer(1, 8)]

    # ===== TABELA PRINCIPAL (fechada) =====
    headers = ["Data", "OS", "Código", "Descrição", "Un", "Qtd", "Preço", "Subtotal"]
    data_rows = [headers]
    for r in linhas:
        data_rows.append([
            r["data"].strftime("%d/%m/%Y") if isinstance(r["data"], date) else r["data"],
            r["os_num"], r["codigo"], r["descricao"], r["un"],
            f"{r['qtd']:.2f}", format_brl(r["preco"]), format_brl(r["subtotal"])
        ])

    W = doc.width
    # Data 9% | OS 14% | Código 12% | Descrição 31% | Un 6% | Qtd 8% | Preço 10% | Subtotal 10%
    col_widths = [0.09*W, 0.14*W, 0.12*W, 0.31*W, 0.06*W, 0.08*W, 0.10*W, 0.10*W]

    tbl = Table(data_rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.black),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID", (0,0), (-1,-1), 0.25, colors.black),
        ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3),  ("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("ALIGN", (0,1), (3,-1), "LEFT"),
        ("ALIGN", (4,1), (4,-1), "CENTER"),
        ("ALIGN", (5,1), (7,-1), "RIGHT"),
    ]))
    story.append(tbl)

    # ===== RESUMO (tabela separada: Código | Descrição | Un | Qtd | Valor Total) =====
    # agrega por (código, descrição, un): soma qtd e soma subtotal
    resumo = {}  # key -> {"qtd": x, "val": y}
    for r in linhas:
        key = (r["codigo"], r["descricao"], r["un"])
        acc = resumo.setdefault(key, {"qtd": 0.0, "val": 0.0})
        acc["qtd"] += float(r.get("qtd", 0.0) or 0.0)
        acc["val"] += float(r.get("subtotal", 0.0) or 0.0)

    story.append(Spacer(1, 10))
    resumo_title = Table([[Paragraph("<b>RESUMO DO PERÍODO</b>", ParagraphStyle(
        "titRES", parent=styleN, fontSize=10.5, leading=12, alignment=TA_CENTER))]],
        colWidths=[doc.width]
    )
    resumo_title.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")),
        ("TEXTCOLOR",(0,0),(-1,-1), colors.black),
        ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ]))
    story.append(resumo_title)

    res_headers = ["Código", "Descrição", "Un", "Qtd", "Valor Total"]
    res_rows = [res_headers]
    # ordena por código, depois descrição
    for (cod, desc, un), acc in sorted(resumo.items(), key=lambda x: (x[0][0], x[0][1])):
        res_rows.append([cod, desc, un, f"{acc['qtd']:.2f}", format_brl(acc['val'])])

    # larguras do resumo
    rW = doc.width
    # Código 14% | Descrição 46% | Un 7% | Qtd 13% | Valor Total 20%
    res_widths = [0.14*rW, 0.46*rW, 0.07*rW, 0.13*rW, 0.20*rW]

    res_tbl = Table(res_rows, colWidths=res_widths, repeatRows=1)
    res_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0), colors.black),
        ("TEXTCOLOR",(0,0),(-1,0), colors.white),
        ("FONTNAME",(0,0),(-1,0), "Helvetica-Bold"),
        ("GRID", (0,0), (-1,-1), 0.25, colors.black),
        ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3), ("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("ALIGN", (0,1), (1,-1), "LEFT"),
        ("ALIGN", (2,1), (2,-1), "CENTER"),
        ("ALIGN", (3,1), (3,-1), "RIGHT"),
        ("ALIGN", (4,1), (4,-1), "RIGHT"),
    ]))
    story.append(res_tbl)

    # ===== QUADRO TOTAL (separado, com bordas) =====
    story.append(Spacer(1, 10))
    total_val = sum(r["subtotal"] for r in linhas) if linhas else 0.0

    total_box = Table(
        [[Paragraph("<b>Total:</b>", styleN), Paragraph(f"<b>{format_brl(total_val)}</b>", styleN)]],
        colWidths=[28*mm, 38*mm]
    )
    total_box.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.75, colors.black),
        ("ALIGN", (0,0), (0,0), "RIGHT"),
        ("ALIGN", (1,0), (1,0), "RIGHT"),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING",(0,0), (-1,-1), 10),
        ("TOPPADDING",  (0,0), (-1,-1), 6),
        ("BOTTOMPADDING",(0,0), (-1,-1), 6),
        ("BACKGROUND", (0,0), (0,0), colors.HexColor("#f5f5f5")),
    ]))

    # wrapper para alinhar à direita
    wrapper = Table([[None, total_box]], colWidths=[doc.width - (28*mm + 38*mm), (28*mm + 38*mm)])
    wrapper.setStyle(TableStyle([
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),  ("BOTTOMPADDING",(0,0),(-1,-1),0),
    ]))
    story.append(wrapper)

    # build
    doc.build(story,
              onFirstPage=lambda c,d:_on_page(c,d,titulo),
              onLaterPages=lambda c,d:_on_page(c,d,titulo))
    return buf.getvalue()

def gerar_pdf_fechamento(cliente_nome: str, periodo_str: str, linhas: list[dict], logo_bytes: bytes | None) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        topMargin=28, bottomMargin=36, leftMargin=14, rightMargin=14
    )
    story = []

    # Cabeçalho vertical
    story += _header_vertical_centralizado()

    # Bloco de informações
    info_tbl = Table([
        [Paragraph(f"<b>Cliente:</b> {cliente_nome}", styleSmall)],
        [Paragraph(f"<b>Período:</b> {periodo_str}", styleSmall)],
    ], colWidths=[doc.width])
    info_tbl.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),2),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
    ]))
    story += [info_tbl, Spacer(1, 6)]

    # Faixa cinza do título
    titulo = "FECHAMENTO POR CLIENTE"
    tit_tbl = Table([[Paragraph(
        f"<b>{titulo}</b>",
        ParagraphStyle("titFEC", parent=styleN, fontSize=11, leading=13, alignment=TA_CENTER)
    )]], colWidths=[doc.width])
    tit_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")),
        ("TEXTCOLOR",(0,0),(-1,-1), colors.black),
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LEFTPADDING",(0,0),(-1,-1),8), ("RIGHTPADDING",(0,0),(-1,-1),8),
    ]))
    story += [tit_tbl, Spacer(1, 8)]

    # --- TABELA PRINCIPAL (fechada) ---
    headers = ["Data", "Obra", "OS", "Código", "Descrição", "Un", "Qtd", "Preço", "Subtotal"]
    data_rows = [headers]
    for r in linhas:
        data_rows.append([
            r["data"].strftime("%d/%m/%Y") if isinstance(r["data"], date) else r["data"],
            r["obra"], r["os_num"], r["codigo"], r["descricao"], r["un"],
            f"{r['qtd']:.2f}", format_brl(r["preco"]), format_brl(r["subtotal"]),
        ])

    W = doc.width
    # Data 7% | Obra 20% | OS 12% | Código 12% | Descrição 20% | Un 6% | Qtd 6% | Preço 9% | Subtotal 8%
    col_widths = [0.07*W, 0.20*W, 0.12*W, 0.12*W, 0.20*W, 0.06*W, 0.06*W, 0.09*W, 0.08*W]

    tbl = Table(data_rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        # cabeçalho preto
        ("BACKGROUND",(0,0),(-1,0), colors.black),
        ("TEXTCOLOR", (0,0),(-1,0), colors.white),
        ("FONTNAME",  (0,0),(-1,0), "Helvetica-Bold"),

        # grade FECHADA
        ("GRID", (0,0), (-1,-1), 0.25, colors.black),

        # paddings e alinhamentos
        ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3),  ("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("ALIGN", (0,1), (4,-1), "LEFT"),
        ("ALIGN", (5,1), (5,-1), "CENTER"),
        ("ALIGN", (6,1), (8,-1), "RIGHT"),
    ]))
    # respiro visual entre OS e Código
    tbl.setStyle(TableStyle([
        ("RIGHTPADDING", (2,1), (2,-1), 10),  # OS
        ("LEFTPADDING",  (3,1), (3,-1), 10),  # Código
    ]))
    story.append(tbl)

    # --- QUADRO DE TOTAL SEPARADO (com bordas) ---
    story.append(Spacer(1, 10))
    total_val = sum(r["subtotal"] for r in linhas) if linhas else 0.0

    total_box = Table(
        [[Paragraph("<b>Total:</b>", styleN), Paragraph(f"<b>{format_brl(total_val)}</b>", styleN)]],
        colWidths=[28*mm, 38*mm]
    )
    total_box.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.75, colors.black),
        ("ALIGN", (0,0), (0,0), "RIGHT"),
        ("ALIGN", (1,0), (1,0), "RIGHT"),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING",(0,0), (-1,-1), 10),
        ("TOPPADDING",  (0,0), (-1,-1), 6),
        ("BOTTOMPADDING",(0,0), (-1,-1), 6),
        ("BACKGROUND", (0,0), (0,0), colors.HexColor("#f5f5f5")),
    ]))

    wrapper = Table([[None, total_box]], colWidths=[doc.width - (28*mm + 38*mm), (28*mm + 38*mm)])
    wrapper.setStyle(TableStyle([
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),  ("BOTTOMPADDING",(0,0),(-1,-1),0),
    ]))
    story.append(wrapper)

    doc.build(story,
              onFirstPage=lambda c,d:_on_page(c,d,titulo),
              onLaterPages=lambda c,d:_on_page(c,d,titulo))
    return buf.getvalue()

# ================= App state =================
if "itens_os_tmp" not in st.session_state: st.session_state["itens_os_tmp"] = []
if "logo_bytes" not in st.session_state: st.session_state["logo_bytes"] = None

# ================= Sidebar =================
with st.sidebar:
    st.header("☑️ Habisolute Engenharia & Controle Tecnológico")
    page = st.radio("Menu", [
        "Emitir OS 🗳️", "Visualizar / Imprimir",
        "Cadastro: Obras", "Cadastro: Serviços", "Cadastro: Clientes",
        "Medição Mensal", "Relatórios"
    ], index=0)
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
    st.markdown("**Logo (PNG/JPG)**")
    up = st.file_uploader(" ", type=["png","jpg","jpeg"], label_visibility="collapsed")
    if up is not None:
        st.session_state["logo_bytes"] = up.read()
        st.success("Logo carregado.")

# ================= Common queries =================
def obter_os_com_itens(sess: Session, os_id: int):
    # Evita N+1 trazendo itens + serviço associado de uma vez
    os_row = sess.query(OS).options(
        selectinload(OS.itens).selectinload(OSItem.servico)
    ).filter(OS.id == os_id).first()
    obra_row = sess.get(Obra, os_row.obra_id)
    itens = []
    for it in os_row.itens:
        sv = it.servico
        itens.append({
            "codigo": sv.codigo, "descricao": sv.descricao, "unidade": sv.unidade,
            "qtd_prev": it.quantidade_prevista or 0.0, "preco_unit": sv.preco_unit or 0.0,
            "subtotal": (sv.preco_unit or 0.0) * (it.quantidade_prevista or 0.0)
        })
    return os_row, obra_row, itens

# ================= Pages =================
def page_clientes():
    st.markdown('<div class="card"><h3>Cadastro de Clientes</h3></div>', unsafe_allow_html=True)
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

        if st.button("➕ Cadastrar Cliente", use_container_width=True):
            if not nome.strip():
                st.error("Informe o nome do cliente.")
            else:
                with SessionLocal() as sess:
                    ja = sess.execute(select(Cliente).where(Cliente.nome == nome.strip())).scalars().first()
                    if ja:
                        st.error("Já existe cliente com esse nome.")
                    else:
                        sess.add(Cliente(
                            nome=nome.strip(), documento=(documento or None),
                            contato=(contato or None), email=(email or None),
                            telefone=(telefone or None), ativo=1 if ativo else 0
                        ))
                        sess.commit()
                        st.success("Cliente cadastrado.")
                        st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    with col_list:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("#### Clientes")

        with SessionLocal() as sess:
            clientes = sess.execute(select(Cliente).order_by(Cliente.nome.asc())).scalars().all()

        if not clientes:
            st.info("Nenhum cliente ainda.")
            st.markdown('</div>', unsafe_allow_html=True); return

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
                if bcol1.button("💾 Salvar alterações", use_container_width=True, key=f"cli_save_{c.id}"):
                    dup = sess.execute(select(Cliente).where(Cliente.nome == c.nome, Cliente.id != c.id)).scalars().first()
                    if dup:
                        st.error("Já existe outro cliente com esse nome.")
                    else:
                        if novo_bloq and not bloqueado_atual:
                            c.bloqueado = 1; c.bloqueado_desde = date.today(); c.bloqueado_motivo = (novo_motivo or "Bloqueado")
                        elif not novo_bloq and bloqueado_atual:
                            c.bloqueado = 0; c.bloqueado_desde = None; c.bloqueado_motivo = None
                        else:
                            c.bloqueado_motivo = (novo_motivo or None)
                        sess.commit(); st.success("Cliente atualizado."); st.rerun()

                with SessionLocal() as s2:
                    obras_vinc = s2.query(Obra).filter(
                        (Obra.cliente_id == c.id) | (func.trim(func.coalesce(Obra.cliente, "")) == c.nome)
                    ).count()
                if obras_vinc > 0:
                    bcol2.button("🗑️ Excluir (bloqueado — possui obras)", disabled=True, use_container_width=True)
                    st.warning(f"Este cliente possui {obras_vinc} obra(s) vinculada(s).")
                else:
                    conf = st.checkbox("Confirmo a exclusão deste cliente", key=f"cli_del_conf_{c.id}")
                    if bcol2.button("🗑️ Excluir cliente", use_container_width=True, disabled=not conf):
                        sess.delete(c); sess.commit(); st.success("Cliente excluído."); st.rerun()

        st.markdown('</div>', unsafe_allow_html=True)

def page_obras():
    st.markdown('<div class="card"><h3>Cadastro de Obras</h3></div>', unsafe_allow_html=True)
    c1, c2 = st.columns([1, 2])

    with c1:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("#### Nova Obra")
        with SessionLocal() as sess:
            clientes = sess.execute(select(Cliente).where(Cliente.ativo == 1).order_by(Cliente.nome.asc())).scalars().all()
        nome = st.text_input("Nome da Obra *", key="obra_new_nome")
        endereco = st.text_input("Endereço *", key="obra_new_end")
        cliente_opt = ["(Sem cliente)"] + [c.nome for c in clientes]
        cliente_sel_nome = st.selectbox("Cliente", cliente_opt, key="obra_new_cli")
        if st.button("➕ Cadastrar Obra", use_container_width=True):
            if not nome or not endereco:
                st.error("Preencha Nome e Endereço.")
            else:
                with SessionLocal() as sess:
                    cid = None
                    if cliente_sel_nome != "(Sem cliente)":
                        cobj = sess.execute(select(Cliente).where(Cliente.nome == cliente_sel_nome)).scalars().first()
                        cid = cobj.id if cobj else None
                    sess.add(Obra(nome=nome.strip(), endereco=endereco.strip(), cliente_id=cid, ativo=1))
                    sess.commit(); st.success("Obra cadastrada."); st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    with c2:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("#### Obras")

        with SessionLocal() as sess:
            obras = sess.execute(
                select(Obra).options(selectinload(Obra.cliente_ref)).order_by(Obra.nome.asc())
            ).scalars().all()

        if not obras:
            st.info("Nenhuma obra cadastrada."); st.markdown('</div>', unsafe_allow_html=True); return

        df = pd.DataFrame([{
            "id": o.id, "nome": o.nome, "endereco": o.endereco,
            "cliente": (o.cliente_ref.nome if getattr(o, "cliente_ref", None) else None),
            "ativo": o.ativo, "bloqueada": o.bloqueada, "motivo": o.bloqueada_motivo, "desde": o.bloqueada_desde
        } for o in obras])
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.markdown("##### Editar / Excluir")
        obra_sel = st.selectbox(
            "Selecione uma obra",
            options=obras,
            format_func=lambda o: f"{o.nome} — {o.endereco} (ID {o.id})",
            key="obra_edit_sel"
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

                b1, b2 = st.columns([1, 1])
                if b1.button("💾 Salvar alterações", use_container_width=True, key=f"obra_save_{o.id}"):
                    if nova_bloq and not bloqueada_atual:
                        o.bloqueada = 1; o.bloqueada_desde = date.today(); o.bloqueada_motivo = (novo_motivo_obra or "Obra bloqueada")
                    elif not nova_bloq and bloqueada_atual:
                        o.bloqueada = 0; o.bloqueada_desde = None; o.bloqueada_motivo = None
                    else:
                        o.bloqueada_motivo = (novo_motivo_obra or None)
                    sess.commit(); st.success("Obra atualizada."); st.rerun()

                with SessionLocal() as sess2:
                    os_count = sess2.query(OS).filter(OS.obra_id == o.id).count()
                if os_count > 0:
                    st.warning(f"Ao excluir esta obra, {os_count} OS serão removidas.")
                conf = st.checkbox("Confirmo a exclusão desta obra (e suas OS)", key=f"obra_del_conf_{o.id}")
                if b2.button("🗑️ Excluir obra", use_container_width=True, disabled=not conf):
                    sess.delete(o); sess.commit(); st.success("Obra excluída."); st.rerun()

        st.markdown('</div>', unsafe_allow_html=True)

def page_servicos():
    c1, c2 = st.columns([1, 2])

    with c1:
        st.markdown('<div class="card"><h3>Cadastro de Serviços</h3></div>', unsafe_allow_html=True)
        st.markdown('<div class="card">', unsafe_allow_html=True)
        codigo = st.text_input("Código *", placeholder="Ex.: CP28, SLUMP, MOLD").strip().upper()
        descricao = st.text_input("Descrição *", placeholder="Ex.: Rompimento de Corpo de Prova 28 dias")
        unidade = st.text_input("Unidade *", value="un")
        preco = st.number_input("Preço unitário (interno) — opcional", min_value=0.0, step=1.0, value=0.0)
        ativo = st.checkbox("Ativo", value=True, key="srv_new_ativo")
        if st.button("➕ Cadastrar Serviço", use_container_width=True):
            if not codigo or not descricao or not unidade:
                st.error("Preencha Código, Descrição e Unidade.")
            else:
                with SessionLocal() as sess:
                    ja = sess.execute(select(Servico).where(Servico.codigo == codigo)).scalars().first()
                    if ja: st.error("Já existe serviço com esse código.")
                    else:
                        sess.add(Servico(codigo=codigo, descricao=descricao, unidade=unidade,
                                         preco_unit=(preco or None), ativo=1 if ativo else 0))
                        sess.commit(); st.success("Serviço cadastrado."); st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    with c2:
        st.markdown('<div class="card"><h3>Serviços cadastrados</h3></div>', unsafe_allow_html=True)
        st.markdown('<div class="card">', unsafe_allow_html=True)
        with SessionLocal() as sess:
            servs = sess.execute(select(Servico).order_by(Servico.codigo.asc())).scalars().all()
        if not servs:
            st.info("Nenhum serviço cadastrado ainda."); st.markdown('</div>', unsafe_allow_html=True); return
        st.dataframe(pd.DataFrame([{
            "id": s.id, "codigo": s.codigo, "descricao": s.descricao,
            "unidade": s.unidade, "preco_unit": s.preco_unit, "ativo": s.ativo
        } for s in servs]), use_container_width=True, hide_index=True)

        st.markdown("##### Editar / Excluir")
        srv_sel = st.selectbox("Selecione um serviço", options=servs,
                               format_func=lambda s: f"{s.codigo} — {s.descricao} (ID {s.id})", key="srv_edit_sel")
        if srv_sel:
            with SessionLocal() as sess:
                s = sess.get(Servico, srv_sel.id)
                e1, e2 = st.columns(2)
                with e1:
                    novo_codigo = st.text_input("Código", value=s.codigo or "", key=f"srv_edit_cod_{s.id}").strip().upper()
                    s.descricao = st.text_input("Descrição", value=s.descricao or "", key=f"srv_edit_desc_{s.id}")
                    s.unidade = st.text_input("Unidade", value=s.unidade or "un", key=f"srv_edit_un_{s.id}")
                with e2:
                    s.preco_unit = st.number_input("Preço unitário", min_value=0.0, step=1.0,
                                                   value=float(s.preco_unit or 0.0), key=f"srv_edit_preco_{s.id}")
                    s.ativo = 1 if st.checkbox("Ativo", value=bool(s.ativo), key=f"srv_edit_ativo_{s.id}") else 0
                b1, b2 = st.columns([1, 1])
                if b1.button("💾 Salvar alterações", use_container_width=True, key=f"srv_save_{s.id}"):
                    dup = sess.execute(select(Servico).where(Servico.codigo == novo_codigo, Servico.id != s.id)).scalars().first()
                    if dup: st.error("Já existe outro serviço com esse código.")
                    else:
                        s.codigo = novo_codigo; sess.commit(); st.success("Serviço atualizado."); st.rerun()
                with SessionLocal() as sess2:
                    itens_count = sess2.query(OSItem).filter(OSItem.servico_id == s.id).count()
                if itens_count > 0:
                    st.warning(f"Ao excluir este serviço, {itens_count} item(ns) de OS serão removidos.")
                conf = st.checkbox("Confirmo a exclusão deste serviço", key=f"srv_del_conf_{s.id}")
                if b2.button("🗑️ Excluir serviço", use_container_width=True, disabled=not conf):
                    sess.delete(s); sess.commit(); st.success("Serviço excluído."); st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

def page_emitir_os():
    st.markdown('<div class="card"><h3>🗳️Emitir OS</h3></div>', unsafe_allow_html=True)

    # --- MENSAGEM PÓS-GERAÇÃO (persiste após rerun) ---
    _last = st.session_state.pop("last_os_msg", None)
    if _last == "success":
        st.success("OS gerado com Sucesso")
        st.toast("OS gerado com Sucesso", icon="✅")
    elif _last == "error":
        st.error("OS não gerada")
        st.toast("OS não gerada", icon="❌")

    with SessionLocal() as sess:
        obras = sess.execute(
            select(Obra).options(selectinload(Obra.cliente_ref)).where(Obra.ativo == 1).order_by(Obra.nome.asc())
        ).scalars().all()
        servs = sess.execute(select(Servico).where(Servico.ativo == 1).order_by(Servico.codigo.asc())).scalars().all()

    if not obras or not servs:
        st.warning("Cadastre ao menos 1 obra e 1 serviço para emitir OS.")
        return

    termo = st.text_input("Pesquisar obra", placeholder="Digite parte do nome/endereço/cliente").strip().lower()

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
        st.info("Nenhuma obra encontrada.")
        return

    idx_escolhido = st.selectbox("Obra *", list(range(len(opt_pairs))), format_func=lambda i: opt_pairs[i][1])
    obra_sel, _lbl, cliente_bloqueado, obra_bloqueada = opt_pairs[idx_escolhido]

    # ==== CAMPO/STATUS DE MEDIÇÃO (em dia / em atraso) ====
    # Regras:
    # - última OS "Medido" -> primeira "Aberta" após ela; se não houver "Medido", usa a primeira OS "Aberta" da obra.
    # - 30+ dias desde a emissão => Medição em atraso.
    try:
        with SessionLocal() as sess:
            ultima_medida_dt = (sess.query(func.max(OS.data_emissao))
                                  .filter(OS.obra_id == obra_sel.id, OS.status == "Medido")
                                  .scalar())
            os_ref = None
            if ultima_medida_dt:
                os_ref = (sess.query(OS)
                            .filter(OS.obra_id == obra_sel.id,
                                    OS.status == "Aberta",
                                    OS.data_emissao > ultima_medida_dt)
                            .order_by(OS.data_emissao.asc(), OS.id.asc())
                            .first())
            else:
                os_ref = (sess.query(OS)
                            .filter(OS.obra_id == obra_sel.id, OS.status == "Aberta")
                            .order_by(OS.data_emissao.asc(), OS.id.asc())
                            .first())

        if os_ref and os_ref.data_emissao:
            dias = (date.today() - os_ref.data_emissao).days
            if dias >= 30:
                st.markdown(
                    f'<div class="metric-container" style="border-color:#FF7A00">'
                    f'<b>Medição em atraso</b>'
                    f'<div style="margin-top:4px">OS <b>{os_ref.numero}</b> em Aberto desde '
                    f'<b>{os_ref.data_emissao.strftime("%d/%m/%Y")}</b> — <b>{dias} dias</b>.</div>'
                    f'</div>', unsafe_allow_html=True
                )
            else:
                st.markdown(
                    f'<div class="metric-container">'
                    f'<b>Medição em dia</b>'
                    f'<div style="margin-top:4px">OS <b>{os_ref.numero}</b> em Aberto há '
                    f'<b>{dias} dias</b> (limite 30).</div>'
                    f'</div>', unsafe_allow_html=True
                )
        else:
            # Sem OS em Aberto (após última medição, ou no geral): consideramos em dia
            st.markdown(
                f'<div class="metric-container">'
                f'<b>Medição em dia</b>'
                f'<div style="margin-top:4px">Nenhuma OS em Aberto pendente de medição.</div>'
                f'</div>', unsafe_allow_html=True
            )
    except Exception:
        # Se algo falhar, não travar a tela
        pass
    # ==== FIM STATUS DE MEDIÇÃO ====

    if cliente_bloqueado:
        with SessionLocal() as sess:
            cli = sess.get(Cliente, obra_sel.cliente_id) if obra_sel.cliente_id else None
        motivo = cli.bloqueado_motivo if cli else "Cliente bloqueado."
        desde = cli.bloqueado_desde.strftime("%d/%m/%Y") if (cli and cli.bloqueado_desde) else "-"
        st.error(f"Cliente **bloqueado** desde {desde}. Motivo: {motivo}. Emissão desabilitada.")
    if obra_bloqueada:
        motivo_o = obra_sel.bloqueada_motivo or "Obra bloqueada."
        desde_o = obra_sel.bloqueada_desde.strftime("%d/%m/%Y") if obra_sel.bloqueada_desde else "-"
        st.error(f"**Obra bloqueada** desde {desde_o}. Motivo: {motivo_o}. Emissão desabilitada.")

    bloqueio_ativo = cliente_bloqueado or obra_bloqueada

    data_emissao = st.date_input("Data de Emissão", value=date.today())
    observ = st.text_area("Observações (opcional)")

    st.markdown("##### Itens da OS")
    c1, c2, c3, c4 = st.columns([2, 4, 1, 2])

    # busca rápida de serviço
    q_srv = c2.text_input("Buscar serviço (código/descrição)", placeholder="ex.: CP28 ou rompimento").strip().lower()
    servs_filtrados = [s for s in servs if q_srv in f"{s.codigo} {s.descricao}".lower()] if q_srv else servs

    serv_sel = c1.selectbox("Serviço", servs_filtrados, format_func=lambda s: f"{s.codigo} — {s.descricao}")
    qtd_prev = c4.number_input("Qtd. prevista", min_value=0.0, step=1.0, value=0.0)

    if c3.button("➕ Adicionar", disabled=bloqueio_ativo):
        if qtd_prev <= 0:
            st.error("Informe uma quantidade prevista > 0.")
        else:
            st.session_state["itens_os_tmp"].append(
                (serv_sel.id, serv_sel.codigo, serv_sel.descricao, serv_sel.unidade, float(qtd_prev))
            )
            st.success("Item adicionado.")
            st.toast("Item adicionado", icon="➕")

    if st.session_state["itens_os_tmp"]:
        df_it = pd.DataFrame(
            st.session_state["itens_os_tmp"],
            columns=["servico_id", "Código", "Descrição", "Un", "Qtd Prevista"]
        )
        st.dataframe(df_it[["Código", "Descrição", "Un", "Qtd Prevista"]], use_container_width=True)
        colA, colB = st.columns([1, 3])
        if colA.button("🧹 Limpar itens"):
            st.session_state["itens_os_tmp"] = []
            st.success("Itens limpos.")
            st.toast("Itens limpos", icon="🧹")
        if colB.button("🧰 Gerar OS", disabled=bloqueio_ativo or not st.session_state["itens_os_tmp"]):
            if bloqueio_ativo:
                st.error("Cliente/Obra bloqueado — libere antes de emitir novas OS.")
            else:
                ok = False
                sess = SessionLocal()
                try:
                    numero = gerar_numero_os(sess)
                    nova = OS(
                        numero=numero,
                        data_emissao=data_emissao,
                        obra_id=obra_sel.id,
                        observacoes=(observ or None),
                        status="Aberta"
                    )
                    sess.add(nova)
                    sess.flush()
                    for (sid, _cod, _desc, _un, qtd) in st.session_state["itens_os_tmp"]:
                        sess.add(OSItem(os_id=nova.id, servico_id=sid, quantidade_prevista=(qtd or None)))
                    sess.commit()
                    ok = True
                except Exception:
                    sess.rollback()
                    ok = False
                finally:
                    sess.close()

                # limpa o carrinho temporário
                st.session_state["itens_os_tmp"] = []
                # seta mensagem e força refresh da página (toast e banner serão exibidos no topo)
                st.session_state["last_os_msg"] = "success" if ok else "error"
                st.rerun()
    else:
        st.info("Adicione itens para gerar a OS.")

    # --- OS Recentes (com nome da obra) ---
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
    st.markdown("##### OS Recentes")
    with SessionLocal() as sess:
        os_df = to_df(sess, OS)
        if not os_df.empty:
            obras_map = {o.id: f"{o.nome} — {o.endereco}" for o in sess.query(Obra).all()}
            os_df["obra_nome"] = os_df["obra_id"].map(lambda oid: obras_map.get(oid, f"Obra {oid}"))
            os_df["data_emissao"] = pd.to_datetime(os_df["data_emissao"], errors="coerce").dt.date
            os_df = os_df.sort_values("id", ascending=False).head(50)
            st.dataframe(
                os_df[["id", "numero", "data_emissao", "obra_nome", "status"]]
                .rename(columns={
                    "id": "ID",
                    "numero": "OS",
                    "data_emissao": "Data",
                    "obra_nome": "Obra",
                    "status": "Status"
                }),
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("Nenhuma OS emitida.")

def page_visualizar_imprimir():
    st.markdown('<div class="card"><h3>Visualizar / Imprimir</h3></div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)

    with SessionLocal() as sess:
        os_df_full = to_df(sess, OS)
        if os_df_full.empty:
            st.info("Nenhuma OS emitida para visualizar."); st.markdown("</div>", unsafe_allow_html=True); return
        os_df_full["data_emissao"] = pd.to_datetime(os_df_full["data_emissao"], errors="coerce").dt.date
        obras_map = {o.id: f"{o.nome} — {o.endereco}" for o in sess.query(Obra).all()}
        os_df_full["obra_nome"] = os_df_full["obra_id"].map(lambda oid: obras_map.get(oid, f"Obra {oid}"))
        os_df_full["data_str"] = os_df_full["data_emissao"].apply(lambda d: d.strftime("%d/%m/%Y") if isinstance(d, date) else "")

    f1, f2, f3 = st.columns([2, 1, 1])
    obra_opcoes = ["(Todas)"] + sorted(os_df_full["obra_nome"].dropna().unique().tolist())
    obra_filtro = f1.selectbox("Filtrar por obra", obra_opcoes)
    status_opcoes = ["(Todos)"] + STATUS_OPTIONS
    status_filtro = f2.selectbox("Status", status_opcoes)

    min_dt = os_df_full["data_emissao"].min(); max_dt = os_df_full["data_emissao"].max()
    hoje = date.today()
    ini_default = min_dt or hoje; fim_default = max_dt or hoje
    if ini_default > fim_default: ini_default, fim_default = fim_default, ini_default
    periodo = f3.date_input("Período", value=(ini_default, fim_default), key="flt_periodo")

    df_view = os_df_full.copy()
    if obra_filtro != "(Todas)": df_view = df_view[df_view["obra_nome"] == obra_filtro]
    if status_filtro != "(Todos)": df_view = df_view[df_view["status"] == status_filtro]
    ini, fim = (periodo if isinstance(periodo, (list, tuple)) and len(periodo) == 2 else (periodo, periodo))
    df_view = df_view[(df_view["data_emissao"] >= ini) & (df_view["data_emissao"] <= fim)]
    df_view = df_view.sort_values(["data_emissao", "id"], ascending=[False, False]).reset_index(drop=True)

    q = st.text_input("Buscar por número da OS", placeholder="ex.: HAB-2025-0012", key="q_os").strip().upper()
    df_filt = df_view if not q else df_view[df_view["numero"].str.contains(q, case=False, na=False)]
    if df_filt.empty:
        st.warning("Nenhuma OS encontrada com os filtros/busca."); st.markdown("</div>", unsafe_allow_html=True); return

    df_filt["label"] = df_filt.apply(lambda r: f"{r['numero']} — {r['obra_nome']} — {r['data_str']} [{r['status']}]", axis=1)
    labels = df_filt["label"].tolist()
    if "os_idx" not in st.session_state or st.session_state.get("q_os_last") != q: st.session_state["os_idx"] = 0
    st.session_state["q_os_last"] = q

    cnav1, csel, cnav2 = st.columns([1, 4, 1])
    with cnav1:
        if st.button("◀ Anterior", use_container_width=True): st.session_state["os_idx"] = (st.session_state["os_idx"] - 1) % len(labels)
    with cnav2:
        if st.button("Próxima ▶", use_container_width=True): st.session_state["os_idx"] = (st.session_state["os_idx"] + 1) % len(labels)
    escolha = csel.selectbox("Selecione a OS para impressão", labels,
                             index=min(st.session_state["os_idx"], len(labels)-1), key="os_select_print")
    st.session_state["os_idx"] = labels.index(escolha)

    row = df_filt.iloc[st.session_state["os_idx"]]
    with SessionLocal() as sess:
        os_row_db = sess.query(OS).filter(OS.id == int(row["id"])).first()
        if not os_row_db:
            st.error("OS não encontrada."); st.markdown("</div>", unsafe_allow_html=True); return
        os_row, obra_row, itens = obter_os_com_itens(sess, os_row_db.id)

    cH1, cH2 = st.columns([2,1])
    with cH1:
        st.write(f"**OS:** {os_row.numero}")
        st.write(f"**Data:** {os_row.data_emissao.strftime('%d/%m/%Y')}")
        st.write(f"**Status:** {os_row.status}")
        st.write(f"**Obra:** {obra_row.nome}")
        st.write(f"**Endereço:** {obra_row.endereco}")
        with SessionLocal() as s2:
            cli = s2.get(Cliente, obra_row.cliente_id) if obra_row.cliente_id else None
        st.write(f"**Cliente:** {(cli.nome if cli else (obra_row.cliente or '-'))}")
        if os_row.observacoes: st.write(f"**Observações:** {os_row.observacoes}")

    total = sum(it["subtotal"] for it in itens)
    with cH2:
        st.markdown(
            f'<div class="metric-container"><b>Total estimado</b><div style="font-size:1.6rem">{format_brl(total)}</div></div>',
            unsafe_allow_html=True
        )

    if itens:
        df_itens = pd.DataFrame(itens).rename(columns={
            "codigo":"Código","descricao":"Descrição","unidade":"Un",
            "qtd_prev":"Qtd Prevista","preco_unit":"Preço Unit.","subtotal":"Subtotal"
        })
        st.dataframe(df_itens[["Código","Descrição","Un","Qtd Prevista","Preço Unit.","Subtotal"]], use_container_width=True)
    else:
        st.info("Esta OS ainda não possui itens.")

    logo_b = st.session_state.get("logo_bytes")
    pdf_interno = gerar_pdf_os(os_row, obra_row, itens, show_prices=True, logo_bytes=logo_b)
    pdf_cliente = gerar_pdf_os(os_row, obra_row, itens, show_prices=False, logo_bytes=logo_b)
    b1, b2 = st.columns(2)
    with b1:
        st.download_button("📄 Baixar PDF (interno — com preços)", data=pdf_interno,
                           file_name=f"{os_row.numero}_interno.pdf", mime="application/pdf")
    with b2:
        st.download_button("📄 Baixar PDF (cliente — sem preços)", data=pdf_cliente,
                           file_name=f"{os_row.numero}_cliente.pdf", mime="application/pdf")
    st.markdown('</div>', unsafe_allow_html=True)

def page_medicao():
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("## Medição Mensal")

    with SessionLocal() as sess:
        obras = sess.execute(select(Obra).where(Obra.ativo == 1).order_by(Obra.nome.asc())).scalars().all()
    if not obras:
        st.info("Cadastre obras para usar a medição mensal."); st.markdown("</div>", unsafe_allow_html=True); return

    obra_sel = st.selectbox("Obra", obras, format_func=lambda o: f"{o.nome} — {o.endereco}")

    # ===== Status de medição (em dia / em atraso) =====
    try:
        with SessionLocal() as sess:
            ultima_medida_dt = (sess.query(func.max(OS.data_emissao))
                                  .filter(OS.obra_id == obra_sel.id, OS.status == "Medido")
                                  .scalar())
            os_ref = None
            if ultima_medida_dt:
                os_ref = (sess.query(OS)
                            .filter(OS.obra_id == obra_sel.id,
                                    OS.status == "Aberta",
                                    OS.data_emissao > ultima_medida_dt)
                            .order_by(OS.data_emissao.asc(), OS.id.asc())
                            .first())
            else:
                os_ref = (sess.query(OS)
                            .filter(OS.obra_id == obra_sel.id, OS.status == "Aberta")
                            .order_by(OS.data_emissao.asc(), OS.id.asc())
                            .first())

        if os_ref and os_ref.data_emissao:
            dias = (date.today() - os_ref.data_emissao).days
            if dias >= 30:
                st.markdown(
                    f'<div class="metric-container" style="border-color:#FF7A00">'
                    f'<b>Medição em atraso</b>'
                    f'<div style="margin-top:4px">OS <b>{os_ref.numero}</b> em Aberto desde '
                    f'<b>{os_ref.data_emissao.strftime("%d/%m/%Y")}</b> — <b>{dias} dias</b>.</div>'
                    f'</div>', unsafe_allow_html=True
                )
            else:
                st.markdown(
                    f'<div class="metric-container">'
                    f'<b>Medição em dia</b>'
                    f'<div style="margin-top:4px">OS <b>{os_ref.numero}</b> em Aberto há '
                    f'<b>{dias} dias</b> (limite 30).</div>'
                    f'</div>', unsafe_allow_html=True
                )
        else:
            st.markdown(
                f'<div class="metric-container">'
                f'<b>Medição em dia</b>'
                f'<div style="margin-top:4px">Nenhuma OS em Aberto pendente de medição.</div>'
                f'</div>', unsafe_allow_html=True
            )
    except Exception:
        pass
    # ===== Fim status =====

    # cliente bloqueado (aviso)
    cliente_bloqueado = False
    with SessionLocal() as s:
        ob = s.get(Obra, obra_sel.id)
        cli = s.get(Cliente, ob.cliente_id) if ob and ob.cliente_id else None
        if cli and cli.bloqueado:
            cliente_bloqueado = True
            motivo = cli.bloqueado_motivo or "Sem motivo informado"
            desde = cli.bloqueado_desde.strftime("%d/%m/%Y") if cli.bloqueado_desde else "-"
            st.warning(f"Cliente **bloqueado** desde {desde}. Motivo: {motivo}. Pode gerar PDF, mas não gravar status.")

    # número da medição (editável; próximo por obra)
    try:
        with SessionLocal() as sess:
            ultimo_num = sess.query(func.max(Medicao.numero)).filter(Medicao.obra_id == obra_sel.id).scalar()
    except Exception:
        ultimo_num = 0
    medicao_num = st.number_input("Número da medição", min_value=1, step=1, value=int((ultimo_num or 0) + 1))

    hoje = date.today()
    primeiro_dia = date(hoje.year, hoje.month, 1)
    ultimo_dia = date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1])
    periodo = st.date_input("Período da medição", value=(primeiro_dia, ultimo_dia))
    ini, fim = (periodo if isinstance(periodo, (list, tuple)) and len(periodo) == 2 else (periodo, periodo))

    st.markdown("#### Filtros")
    col_fs1, col_fs2 = st.columns([1, 1])
    with col_fs1:
        status_listagem = st.selectbox("Status das OS a listar", ["(Todos)"] + STATUS_OPTIONS, index=0)
    with col_fs2:
        status_aplicar = st.selectbox("Status para aplicar em massa", STATUS_OPTIONS,
                                      index=STATUS_OPTIONS.index("Medido") if "Medido" in STATUS_OPTIONS else 0,
                                      help="Será gravado nas OS do período ao clicar no botão.")

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
        linhas.append({
            "data": os_row.data_emissao, "os_num": os_row.numero, "status": os_row.status,
            "codigo": sv.codigo, "descricao": sv.descricao, "un": sv.unidade,
            "qtd": (it.quantidade_prevista or 0.0),
            "preco": (sv.preco_unit or 0.0),
            "subtotal": (sv.preco_unit or 0.0) * (it.quantidade_prevista or 0.0),
        })

    st.markdown("#### Itens do período (após filtros)")
    if not linhas:
        st.info("Não há itens para as condições selecionadas.")
    else:
        df = pd.DataFrame(linhas)
        total = df["subtotal"].sum()
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
            st.markdown('<div class="metric-container"><b>Total do período</b>'
                        f'<div style="font-size:1.6rem">{format_brl(total)}</div></div>', unsafe_allow_html=True)

        period_text = f"{ini.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}"
        logo_b = st.session_state.get("logo_bytes")
        c1, c2, _ = st.columns([1,1,2])
        with c1:
            pdf = gerar_pdf_medicao(obra_sel.nome, period_text, linhas, logo_bytes=logo_b, medicao_num=int(medicao_num))
            st.download_button("📄 Gerar PDF da Medição", data=pdf,
                               file_name=f"medicao_{obra_sel.id}_{ini}_{fim}.pdf", mime="application/pdf")
        with c2:
            btn_label = f"✅ Aplicar status '{status_aplicar}' a todas as OS do período"
            if st.button(btn_label, disabled=cliente_bloqueado):
                if cliente_bloqueado:
                    st.error("Cliente bloqueado — libere antes de atualizar o status.")
                else:
                    with SessionLocal() as sess:
                        sess.query(OS).filter(
                            OS.obra_id == obra_sel.id,
                            OS.data_emissao >= ini, OS.data_emissao <= fim
                        ).update({OS.status: status_aplicar}, synchronize_session="fetch")
                        sess.add(Medicao(obra_id=obra_sel.id, numero=int(medicao_num),
                                         periodo_ini=ini, periodo_fim=fim, criado_em=date.today()))
                        sess.commit()
                    st.success(f"Todas as OS do período foram marcadas como '{status_aplicar}'.")
                    st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

def page_relatorios():
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("## Relatórios por Cliente")

    with SessionLocal() as sess:
        clientes = sess.execute(select(Cliente).where(Cliente.ativo == 1).order_by(Cliente.nome.asc())).scalars().all()
    if not clientes:
        st.info("Cadastre clientes para usar os relatórios."); st.markdown("</div>", unsafe_allow_html=True); return

    cliente_sel = st.selectbox("Cliente", clientes, format_func=lambda c: c.nome)

    if bool(getattr(cliente_sel, "bloqueado", 0)):
        motivo = cliente_sel.bloqueado_motivo or "Sem motivo informado"
        desde = cliente_sel.bloqueado_desde.strftime("%d/%m/%Y") if cliente_sel.bloqueado_desde else "-"
        st.warning(f"Cliente **bloqueado** desde {desde}. Motivo: {motivo}. Relatórios continuam disponíveis.")

    hoje = date.today()
    primeiro_dia = date(hoje.year, hoje.month, 1)
    ultimo_dia = date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1])
    periodo = st.date_input("Período", value=(primeiro_dia, ultimo_dia), key="rel_periodo")
    ini, fim = (periodo if isinstance(periodo, (list, tuple)) and len(periodo) == 2 else (periodo, periodo))

    status_opt = ["(Todos)"] + STATUS_OPTIONS
    status_filtro = st.selectbox("Filtrar por status das OS", status_opt, index=0)

    # ===== Painel de status por obra (Medição em dia/atraso) =====
    with SessionLocal() as sess:
        obras_cliente = sess.execute(
            select(Obra).where(
                (Obra.cliente_id == cliente_sel.id) | (func.trim(func.coalesce(Obra.cliente, "")) == cliente_sel.nome)
            ).order_by(Obra.nome.asc())
        ).scalars().all()

    if obras_cliente:
        resumo_status = []
        with SessionLocal() as sess:
            for ob in obras_cliente:
                ultima_medida_dt = (sess.query(func.max(OS.data_emissao))
                                      .filter(OS.obra_id == ob.id, OS.status == "Medido")
                                      .scalar())
                os_ref = None
                if ultima_medida_dt:
                    os_ref = (sess.query(OS)
                                .filter(OS.obra_id == ob.id,
                                        OS.status == "Aberta",
                                        OS.data_emissao > ultima_medida_dt)
                                .order_by(OS.data_emissao.asc(), OS.id.asc())
                                .first())
                else:
                    os_ref = (sess.query(OS)
                                .filter(OS.obra_id == ob.id, OS.status == "Aberta")
                                .order_by(OS.data_emissao.asc(), OS.id.asc())
                                .first())

                if os_ref and os_ref.data_emissao:
                    dias = (date.today() - os_ref.data_emissao).days
                    status_txt = "Medição em atraso" if dias >= 30 else "Medição em dia"
                    resumo_status.append({
                        "Obra": ob.nome,
                        "Endereço": ob.endereco,
                        "OS (referência)": os_ref.numero,
                        "Emissão": os_ref.data_emissao.strftime("%d/%m/%Y"),
                        "Dias": dias,
                        "Status de Medição": status_txt
                    })
                else:
                    resumo_status.append({
                        "Obra": ob.nome,
                        "Endereço": ob.endereco,
                        "OS (referência)": "-",
                        "Emissão": "-",
                        "Dias": "-",
                        "Status de Medição": "Medição em dia"
                    })

        st.markdown("#### Status de Medição por Obra")
        df_status = pd.DataFrame(resumo_status)
        if not df_status.empty:
            # Ordena: atrasos primeiro
            def _ord(v):
                return 0 if v == "Medição em atraso" else 1
            df_status = df_status.sort_values(["Status de Medição", "Obra"], key=lambda s: s.map(_ord) if s.name == "Status de Medição" else s)
            st.dataframe(df_status, use_container_width=True, hide_index=True)
        else:
            st.info("Sem obras vinculadas ao cliente.")
    else:
        st.warning("Não há obras vinculadas a este cliente."); st.markdown("</div>", unsafe_allow_html=True); return

    # ===== Fechamento detalhado (itens) =====
    with SessionLocal() as sess:
        obra_ids = [o.id for o in obras_cliente]
    with SessionLocal() as sess:
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
        linhas.append({
            "data": os_row.data_emissao, "obra": ob.nome, "os_num": os_row.numero,
            "codigo": sv.codigo, "descricao": sv.descricao, "un": sv.unidade,
            "qtd": (it.quantidade_prevista or 0.0),
            "preco": (sv.preco_unit or 0.0),
            "subtotal": (sv.preco_unit or 0.0) * (it.quantidade_prevista or 0.0),
        })

    st.markdown("#### Fechamento detalhado")
    if not linhas:
        st.info("Nenhum item encontrado para os filtros informados.")
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
            st.markdown('<div class="metric-container"><b>Total geral</b>'
                        f'<div style="font-size:1.6rem">{format_brl(total)}</div></div>', unsafe_allow_html=True)

        logo_b = st.session_state.get("logo_bytes")
        periodo_texto = f"{ini.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}"
        pdf = gerar_pdf_fechamento(cliente_sel.nome, periodo_texto, linhas, logo_bytes=logo_b)
        st.download_button("🧾 Imprimir fechamento (PDF)", data=pdf,
                           file_name=f"fechamento_{cliente_sel.nome}_{ini}_{fim}.pdf",
                           mime="application/pdf")
    st.markdown("</div>", unsafe_allow_html=True)

# ================= Router =================
with st.sidebar:
    st.caption("© Habisolute")

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
else:
    page_emitir_os()
