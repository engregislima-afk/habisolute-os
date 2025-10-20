# -*- coding: utf-8 -*-
# Habisolute — Sistema de OS (Streamlit)
# Visual Fluent/Windows 11 + banners + avisos + medição em dias + APIBrasil (auto)

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
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
try:
    from reportlab.platypus import KeepTogether
except Exception:
    from reportlab.platypus.flowables import KeepTogether

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
PERMS_DB   = PREFS_DIR / "perms.json"
PREFS_PATH = PREFS_DIR / "prefs.json"

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

def load_user_prefs(key: str="default")->Dict[str,Any]:
    return _load_all_prefs().get(key,{})

def save_user_prefs(prefs: Dict[str,Any], key: str="default")->None:
    data=_load_all_prefs(); data[key]=prefs; _save_all_prefs(data)

# ---------- APIBrasil token: descoberta automática ----------
def _discover_apibr_token() -> Optional[str]:
    # 1) st.secrets
    try:
        if "APIBRASIL_TOKEN" in st.secrets:
            v = str(st.secrets["APIBRASIL_TOKEN"]).strip()
            if v: return v
        if "apibrasil" in st.secrets and "token" in st.secrets["apibrasil"]:
            v = str(st.secrets["apibrasil"]["token"]).strip()
            if v: return v
    except Exception:
        pass
    # 2) variáveis de ambiente
    for k in ("APIBRASIL_TOKEN","APIBR_TOKEN","APIBRASIL_BEARER","APIBRASIL"):
        v = os.environ.get(k)
        if v and str(v).strip():
            return str(v).strip()
    # 3) prefs.json
    try:
        p = load_user_prefs().get("apibrasil_token")
        if p: return str(p).strip()
    except Exception:
        pass
    # 4) arquivo .hab_os/apibrasil.token
    f = PREFS_DIR / "apibrasil.token"
    if f.exists():
        try:
            t = f.read_text(encoding="utf-8").strip()
            if t: return t
        except Exception:
            pass
    return None

def _prefs_get_token() -> Optional[str]:
    return _discover_apibr_token()

def _prefs_set_token(value: Optional[str]) -> None:
    prefs = load_user_prefs()
    if value:
        prefs["apibrasil_token"] = value.strip()
        # também grava um arquivo simples para compatibilidade
        try: (PREFS_DIR / "apibrasil.token").write_text(value.strip(), encoding="utf-8")
        except Exception: pass
    else:
        prefs.pop("apibrasil_token", None)
        try:
            (PREFS_DIR / "apibrasil.token").unlink(missing_ok=True)
        except Exception:
            pass
    save_user_prefs(prefs)

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
# CSS / Banners
# =============================================================================
# (… o CSS do seu app permanece igual …)
# ——— para abreviar, mantenha aqui o mesmo bloco _inject_css, banner, flash, etc. ———

# =============================================================================
# Login / Header (iguais ao seu código anterior)
# =============================================================================
# … tudo igual …

# =============================================================================
# DB (SQLite) — modelos e ensures (iguais ao seu código anterior)
# =============================================================================
Base = declarative_base()

class User(Base): ...
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

class Obra(Base): ...
class Servico(Base): ...
class ObraServico(Base): ...
class OS(Base): ...
class OSItem(Base): ...
class Medicao(Base): ...

DB_PATH = Path(__file__).with_name("os_habisolute.db")
engine = create_engine(f"sqlite:///{DB_PATH}", future=True, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base.metadata.create_all(engine)

# … PRAGMAs e funções _ensure_* (iguais) …

# =============================================================================
# Helpers
# =============================================================================
STATUS_OPTIONS = ["Aberta", "Em Execução", "Medido em Aberto", "Medido", "Concluída", "Cancelada"]

def format_brl(v: float) -> str:
    try: return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception: return "R$ 0,00"

# ---------- Normalização e detecção de CNPJ ----------
def _only_digits(txt: str) -> str:
    return re.sub(r"\D+", "", txt or "")

def _looks_like_cnpj(txt: str) -> bool:
    return len(_only_digits(txt)) == 14

# ---------- Cliente APIBrasil ----------
import urllib.request, urllib.error

def buscar_cnpj_apibrasil(doc: str, silent: bool=False) -> Tuple[bool, Dict[str, Any] | str]:
    """
    Retorna (True, dados) em caso de sucesso; (False, msg) caso contrário.
    Token é descoberto automaticamente (_discover_apibr_token).
    """
    cnpj = _only_digits(doc)
    if not cnpj or len(cnpj) not in (11, 14):
        return (False, "Documento inválido (informe um CNPJ/CPF).")

    token = _discover_apibr_token()
    if not token:
        return (False, "Token da APIBrasil não configurado.")

    # endpoint CNPJ da APIBrasil
    url = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}"  # compatível com APIBrasil-proxy
    req = urllib.request.Request(url)
    # alguns proxies da APIBrasil exigem Bearer; manter cabeçalho se existir
    req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(body)
            # normaliza chaves mais usadas
            out = {
                "razao_social": data.get("razao_social") or data.get("nome") or data.get("razaoSocial"),
                "nome_fantasia": data.get("nome_fantasia") or data.get("fantasia") or data.get("nomeFantasia"),
                "telefone": data.get("telefone") or data.get("ddd_telefone_1") or data.get("ddd_telefone"),
                "email": data.get("email"),
            }
            return (True, out)
    except urllib.error.HTTPError as e:
        return (False, f"Erro HTTP {e.code} na APIBrasil.")
    except Exception:
        return (False, "Falha ao consultar a APIBrasil.")

# ---------- autofill ao digitar CNPJ ----------
def _auto_fill_from_cnpj(field_key: str, target_prefix: str, edit_id: int|None=None):
    """
    Se o conteúdo do campo 'field_key' parecer CNPJ e houver token, busca e
    preenche '..._nome' (e-mail/telefone se vierem).
    target_prefix: 'cli_new' ou f'cli_edit_nome_{id}' base.
    """
    txt = st.session_state.get(field_key, "")
    prev_key = f"{field_key}__prev"
    if st.session_state.get(prev_key) == txt:
        return
    st.session_state[prev_key] = txt

    if not _looks_like_cnpj(txt):
        return
    if not _discover_apibr_token():
        return

    ok, res = buscar_cnpj_apibrasil(txt, silent=True)
    if not ok:
        return
    nome_api = (res.get("razao_social") or res.get("nome_fantasia") or "").strip()
    if not nome_api:
        return

    if target_prefix == "cli_new":
        st.session_state["cli_new_nome"] = nome_api
        if res.get("email"): st.session_state["cli_new_email"] = res["email"]
        if res.get("telefone"): st.session_state["cli_new_tel"] = str(res["telefone"])
    else:
        # edição
        if edit_id is not None:
            st.session_state[f"cli_edit_nome_{edit_id}"] = nome_api
# ===================== PÁGINAS: Cadastros =====================

@require_perm("relatorios_export")
def page_clientes():
    st.markdown('<div class="section-title">Cadastro de Clientes</div>', unsafe_allow_html=True)

    # Preferências (opcional): continua disponível, mas agora o token é descoberto sozinho.
    with st.expander("⚙️ Preferências de Integração (APIBrasil)", expanded=False):
        tok = st.text_input("Token da APIBrasil (Bearer)", type="password", value=_prefs_get_token() or "")
        c1, c2 = st.columns(2)
        if c1.button("Salvar token", key="btn_save_apibr_token"):
            _prefs_set_token(tok.strip() or None)
            flash("success", "Token salvo."); _rerun()
        if c2.button("Limpar token", key="btn_clear_apibr_token"):
            _prefs_set_token(None); flash("info", "Token removido."); _rerun()

    col_new, col_list = st.columns([1, 2])

    # ---------- NOVO CLIENTE ----------
    with col_new:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("#### Novo Cliente")

        doc_col, _ = st.columns([2,1])
        documento = doc_col.text_input("Documento (CNPJ/CPF) — opcional", key="cli_new_doc")
        # AUTO: ao digitar CNPJ válido, busca e preenche
        _auto_fill_from_cnpj("cli_new_doc", "cli_new")

        nome = st.text_input("Nome do Cliente *", key="cli_new_nome")
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
                            nome=nome.strip(),
                            documento=(documento or None),
                            contato=(contato or None),
                            email=(email or None),
                            telefone=(telefone or None),
                            ativo=1 if ativo else 0
                        ))
                        sess.commit()
                        flash("success", "Cliente cadastrado.")
                        _rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # ---------- LISTA / EDIÇÃO ----------
    with col_list:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("#### Clientes")
        with SessionLocal() as sess:
            clientes = sess.execute(select(Cliente).order_by(Cliente.nome.asc())).scalars().all()

        if not clientes:
            banner("info", "Nenhum cliente ainda.")
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

                top_cnpj = st.text_input("CNPJ para consulta — opcional", value=c.documento or "", key=f"cli_edit_doc_{c.id}")
                # AUTO: ao digitar CNPJ na edição, preenche nome
                _auto_fill_from_cnpj(f"cli_edit_doc_{c.id}", "cli_edit", edit_id=c.id)

                e1, e2 = st.columns(2)
                with e1:
                    c.nome = st.text_input("Nome", value=c.nome or "", key=f"cli_edit_nome_{c.id}")
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

                bcol1, bcol2 = st.columns([1,]()
