# -*- coding: utf-8 -*-
# Habisolute — Sistema de OS (Streamlit)
# Visual Fluent/Windows 11 + banners + avisos + medição em dias + APIBrasil (auto)

import os, re, json, urllib.request, urllib.error
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
import pandas as pd

# SQLAlchemy
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date, ForeignKey, Text,
    select, func
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

# -----------------------------------------------------------------------------
# Identidade / Config
# -----------------------------------------------------------------------------
SYSTEM_NAME = "Habisolute — Sistema de OS"
SYSTEM_CODE = "hab_os"
BRAND_COLOR = "#f97316"

st.set_page_config(page_title=SYSTEM_NAME, page_icon="🧱", layout="wide")

BASE_DIR   = Path(__file__).resolve().parent
PREFS_DIR  = BASE_DIR / f".{SYSTEM_CODE}"; PREFS_DIR.mkdir(parents=True, exist_ok=True)
USERS_DB   = PREFS_DIR / "users.json"
AUDIT_LOG  = PREFS_DIR / "audit.jsonl"
PERMS_DB   = PREFS_DIR / "perms.json"
PREFS_PATH = PREFS_DIR / "prefs.json"

# -----------------------------------------------------------------------------
# Preferências simples (persistidas em .hab_os/prefs.json)
# -----------------------------------------------------------------------------
def _save_all_prefs(data: Dict[str, Any]) -> None:
    PREFS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

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

# -----------------------------------------------------------------------------
# Descoberta automática do token da APIBrasil
# -----------------------------------------------------------------------------
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
    # 2) environment
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
    # 4) arquivo simples
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
        try: (PREFS_DIR / "apibrasil.token").write_text(value.strip(), encoding="utf-8")
        except Exception: pass
    else:
        prefs.pop("apibrasil_token", None)
        try: (PREFS_DIR / "apibrasil.token").unlink(missing_ok=True)
        except Exception: pass
    save_user_prefs(prefs)

# -----------------------------------------------------------------------------
# Auditoria
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Estado / Tema
# -----------------------------------------------------------------------------
s = st.session_state
s.setdefault("logged_in", True)        # para demo: logado por padrão
s.setdefault("username", "admin")
s.setdefault("is_admin", True)
s.setdefault("role", "admin")
s.setdefault("must_change", False)
s.setdefault("theme_mode", load_user_prefs().get("theme_mode", "Claro"))
s.setdefault("brand", load_user_prefs().get("brand", "Laranja"))
s.setdefault("_flash", [])

def _rerun():
    try: st.rerun()
    except Exception:
        try: st.experimental_rerun()
        except Exception: pass

# -----------------------------------------------------------------------------
# CSS (look Windows 11) + banners/flash
# -----------------------------------------------------------------------------
def _inject_css():
    st.markdown(
        f"""
<style>
:root {{
  --brand: {BRAND_COLOR};
}}
html, body, [class*="block-container"] {{
  background: {"#0f1116" if s.theme_mode=="Escuro" else "#fafafa"};
  color: {"#f5f5f5" if s.theme_mode=="Escuro" else "#111"};
}}
.hb-card {{
  background: {"#141821" if s.theme_mode=="Escuro" else "#fff"};
  border: 1px solid {"#273043" if s.theme_mode=="Escuro" else "rgba(0,0,0,.08)"};
  border-radius: 16px; padding: 14px;
}}
.section-title {{ font-size: 20px; font-weight: 800; margin: 4px 0 8px 0; }}
.card {{ background: var(--card,#fff); border:1px solid rgba(0,0,0,.08); border-radius:14px; padding:14px; margin-bottom:10px; }}
.banner {{
  padding:10px 12px; border-radius:12px; margin:8px 0; font-weight:600;
}}
.banner-info {{ background: rgba(59,130,246,.12); color:#60a5fa; border:1px solid rgba(59,130,246,.25); }}
.banner-warn {{ background: rgba(245,158,11,.12); color:#f59e0b; border:1px solid rgba(245,158,11,.25); }}
.banner-error{{ background: rgba(239,68,68,.12);  color:#f87171; border:1px solid rgba(239,68,68,.25); }}
.banner-success{{ background: rgba(16,185,129,.12); color:#34d399; border:1px solid rgba(16,185,129,.25); }}
.stButton>button, .stDownloadButton>button {{
  background: var(--brand); color:#111; border:none; border-radius: 12px;
  padding:.6rem 1rem; font-weight:800; box-shadow:0 8px 18px rgba(249,115,22,.25);
}}
.stButton>button:disabled, .stDownloadButton>button:disabled {{
  opacity:.55; box-shadow:none;
}}
.hb-side-title {{ display:flex; gap:8px; align-items:center; font-weight:800; margin:6px 0 10px; }}
.hb-dot {{ width:8px; height:8px; background:var(--brand); border-radius:50%; display:inline-block }}
</style>
""",
        unsafe_allow_html=True,
    )

def banner(kind: str, text: str):
    cls = {
        "info": "banner-info",
        "warn": "banner-warn",
        "error": "banner-error",
        "success": "banner-success",
    }.get(kind, "banner-info")
    st.markdown(f'<div class="banner {cls}">{text}</div>', unsafe_allow_html=True)

def flash(kind: str, text: str):
    s._flash.append((kind, text))

def flash_render():
    if s._flash:
        for k,t in s._flash: banner(k,t)
        s._flash.clear()

_inject_css()

# -----------------------------------------------------------------------------
# Permissões mínimas
# -----------------------------------------------------------------------------
DEFAULT_PERMS = {
    "admin": ["*"],
    "gestor": ["os_create","os_view","relatorios_export","dashboard_view"],
    "usuario": ["os_create","os_view","dashboard_view"],
    "diretoria": ["relatorios_export","dashboard_view"],
}

def has_perm(user: str, role: str, perm: str) -> bool:
    if s.get("is_admin"): return True
    perms = DEFAULT_PERMS.get(role or "usuario", [])
    return "*" in perms or perm in perms

def require_perm(perm: str):
    def deco(fn):
        def wrapper(*args, **kwargs):
            if has_perm(s.get("username",""), s.get("role","usuario"), perm):
                return fn(*args, **kwargs)
            banner("error", f"Sem permissão ({perm}).")
        return wrapper
    return deco

# Header simples (você pode trocar por login real)
st.sidebar.markdown("### Usuário")
st.sidebar.write(f"👤 **{s.username}** — *{s.role}*")
# =============================================================================
# DB (SQLite) — modelos
# =============================================================================
Base = declarative_base()

# Se quiser uma tabela de usuários depois, troque este mixin por um modelo completo
class User:
    __abstract__ = True

class Cliente(Base):
    __tablename__ = "clientes"

    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False, unique=True)

    documento = Column(String)
    contato   = Column(String)
    email     = Column(String)
    telefone  = Column(String)

    ativo            = Column(Integer, default=1)
    bloqueado        = Column(Integer, default=0)
    bloqueado_motivo = Column(Text)
    bloqueado_desde  = Column(Date)

    obras = relationship("Obra", back_populates="cliente_ref", cascade="all, delete-orphan")

class Obra(Base):
    __tablename__ = "obras"

    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False)

    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=True)
    cliente    = Column(String, nullable=True)   # compatível com filtro por nome

    cliente_ref = relationship("Cliente", back_populates="obras")

class Servico(Base):
    __tablename__ = "servicos"

    id   = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False, unique=True)
    unidade = Column(String)
    preco_unitario = Column(Float, default=0.0)

class ObraServico(Base):
    __tablename__ = "obra_servicos"

    id = Column(Integer, primary_key=True)
    obra_id    = Column(Integer, ForeignKey("obras.id"), nullable=False)
    servico_id = Column(Integer, ForeignKey("servicos.id"), nullable=False)
    quant      = Column(Float, default=0.0)

class OS(Base):
    __tablename__ = "os"

    id = Column(Integer, primary_key=True)
    obra_id = Column(Integer, ForeignKey("obras.id"), nullable=False)
    emissao = Column(Date, default=date.today)
    status  = Column(String, default="Aberta")

class OSItem(Base):
    __tablename__ = "os_itens"

    id = Column(Integer, primary_key=True)
    os_id      = Column(Integer, ForeignKey("os.id"), nullable=False)
    servico_id = Column(Integer, ForeignKey("servicos.id"), nullable=False)
    quantidade = Column(Float, default=0.0)
    preco      = Column(Float, default=0.0)

class Medicao(Base):
    __tablename__ = "medicoes"

    id = Column(Integer, primary_key=True)
    obra_id = Column(Integer, ForeignKey("obras.id"), nullable=False)
    competencia = Column(String, nullable=True)  # "2025-10"
    data_ref    = Column(Date, nullable=True)
    valor       = Column(Float, default=0.0)

# Engine/Session
DB_PATH = Path(__file__).with_name("os_habisolute.db")
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    future=True,
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base.metadata.create_all(engine)

# =============================================================================
# Helpers gerais
# =============================================================================
STATUS_OPTIONS = ["Aberta", "Em Execução", "Medido em Aberto", "Medido", "Concluída", "Cancelada"]

def format_brl(v: float) -> str:
    try: return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception: return "R$ 0,00"

def _only_digits(txt: str) -> str:
    return re.sub(r"\D+", "", txt or "")

def _looks_like_cnpj(txt: str) -> bool:
    return len(_only_digits(txt)) == 14

# =============================================================================
# APIBrasil — consulta CNPJ (token auto)
# =============================================================================
def buscar_cnpj_apibrasil(doc: str, silent: bool=False) -> Tuple[bool, Dict[str, Any] | str]:
    cnpj = _only_digits(doc)
    if not cnpj or len(cnpj) not in (11, 14):
        return (False, "Documento inválido (informe um CNPJ/CPF).")

    token = _discover_apibr_token()
    if not token:
        return (False, "Token da APIBrasil não configurado.")

    url = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}"  # compatível com proxy da APIBrasil
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(body)
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

def _auto_fill_from_cnpj(field_key: str, target_prefix: str, edit_id: int|None=None):
    txt = st.session_state.get(field_key, "")
    prev_key = f"{field_key}__prev"
    if st.session_state.get(prev_key) == txt:
        return
    st.session_state[prev_key] = txt

    if not _looks_like_cnpj(txt): return
    if not _discover_apibr_token(): return

    ok, res = buscar_cnpj_apibrasil(txt, silent=True)
    if not ok: return
    nome_api = (res.get("razao_social") or res.get("nome_fantasia") or "").strip()
    if not nome_api: return

    if target_prefix == "cli_new":
        st.session_state["cli_new_nome"] = nome_api
        if res.get("email"): st.session_state["cli_new_email"] = res["email"]
        if res.get("telefone"): st.session_state["cli_new_tel"] = str(res["telefone"])
    else:
        if edit_id is not None:
            st.session_state[f"cli_edit_nome_{edit_id}"] = nome_api

# =============================================================================
# Página — Clientes
# =============================================================================
@require_perm("relatorios_export")
def page_clientes():
    st.markdown('<div class="section-title">Cadastro de Clientes</div>', unsafe_allow_html=True)

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

                st.text_input("CNPJ para consulta — opcional", value=c.documento or "", key=f"cli_edit_doc_{c.id}")
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
                        c.documento = st.session_state.get(f"cli_edit_doc_{c.id}") or c.documento
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
# =============================================================================
# Demais páginas (stubs prontos para você completar)
# =============================================================================
def page_obras():
    st.markdown('<div class="section-title">Cadastro de Obras</div>', unsafe_allow_html=True)
    banner("info", "Stub: implemente aqui o cadastro de obras.")

def page_servicos():
    st.markdown('<div class="section-title">Cadastro de Serviços</div>', unsafe_allow_html=True)
    banner("info", "Stub: implemente aqui o cadastro de serviços.")

def page_emitir_os():
    st.markdown('<div class="section-title">Emitir OS</div>', unsafe_allow_html=True)
    banner("info", "Stub: emissão de OS.")

def page_visualizar_imprimir():
    st.markdown('<div class="section-title">Visualizar / Imprimir OS</div>', unsafe_allow_html=True)
    banner("info", "Stub: listagem e impressão de OS.")

def page_medicao():
    st.markdown('<div class="section-title">Medição Mensal</div>', unsafe_allow_html=True)
    banner("info", "Stub: medição mensal.")

def page_relatorios():
    st.markdown('<div class="section-title">Relatórios</div>', unsafe_allow_html=True)
    banner("info", "Stub: relatórios.")

def page_export():
    st.markdown('<div class="section-title">Exportações</div>', unsafe_allow_html=True)
    banner("info", "Stub: exportações.")

# =============================================================================
# Menu / Router
# =============================================================================
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
    "Emitir OS","Cadastro: Clientes","Cadastro: Obras","Cadastro: Serviços",
    "Visualizar / Imprimir","Medição Mensal","Relatórios","Exportações",
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
