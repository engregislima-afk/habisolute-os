# -*- coding: utf-8 -*-
import re, sys, shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent

def read(p): return Path(p).read_text(encoding="utf-8")
def write(p, s): Path(p).write_text(s, encoding="utf-8")

def patch_sidebar(code: str) -> str:
    # Garante sidebar expandida
    code = re.sub(
        r"st\.set_page_config\(([^)]*?)\)",
        lambda m: (
            "st.set_page_config(" +
            (m.group(1) + ", " if m.group(1).strip() else "") +
            "initial_sidebar_state=\"expanded\")"
            if "initial_sidebar_state" not in m.group(0)
            else m.group(0)
        ),
        code,
        count=1,
        flags=re.DOTALL
    )
    return code

def patch_flash(code: str) -> str:
    # Corrige uso de função inexistente em flash()
    buggy = r"def flash\(kind: str, text: str, button: dict \| None = None\):\s*?\n\s*q = st\.session_state_inject_css\(s\.get\(\"theme_mode\"\)\)\s*?\n\s*q\.append\(\{"
    if re.search(buggy, code, flags=re.DOTALL):
        code = re.sub(
            buggy,
            'def flash(kind: str, text: str, button: dict | None = None):\n    q = st.session_state.get("_flash", [])\n    st.session_state["_flash"] = q\n    q.append({',
            code,
            count=1,
            flags=re.DOTALL
        )
    return code

def patch_indexes(code: str) -> str:
    # Substitui bloco de CREATE INDEX por versão segura
    needle = (
        'with engine.begin() as conn:\\n'
        '    conn.exec_driver_sql("PRAGMA journal_mode=WAL;")\\n'
        '    conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")\\n'
        '    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_os_obra_data ON os(obra_id, data_emissao);")\\n'
        '    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_os_status ON os(status);")\\n'
        '    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_os_numero ON os(numero);")\\n'
        '    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_ositem_osid ON os_itens(os_id);")\\n'
        '    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_medicoes_obra ON medicoes(obra_id);")'
    )
    replacement = r"""
def _safe_create_index(conn, idx_name: str, table: str, cols: str):
    try:
        t = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if not t:
            return
        row = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='index' AND name=?", (idx_name,)).fetchone()
        if row:
            return
        conn.exec_driver_sql(f"CREATE INDEX {idx_name} ON {table}({cols})")
    except Exception:
        pass

with engine.begin() as conn:
    try:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
        conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    _safe_create_index(conn, "ix_os_obra_data", "os", "obra_id, data_emissao")
    _safe_create_index(conn, "ix_os_status", "os", "status")
    _safe_create_index(conn, "ix_os_numero", "os", "numero")
    _safe_create_index(conn, "ix_ositem_osid", "os_itens", "os_id")
    _safe_create_index(conn, "ix_medicoes_obra", "medicoes", "obra_id")
""".strip("\n")
    if needle in code:
        return code.replace(needle, replacement, 1)
    # Se o bloco exato não for encontrado, injeta após o create_all
    return code.replace("Base.metadata.create_all(engine)", "Base.metadata.create_all(engine)\n" + replacement)

def patch_inline_nav(code: str) -> str:
    # Adiciona “Navegação rápida” horizontal logo após o radio da sidebar
    anchor = 'page = st.sidebar.radio("Ir para", MENU, index=0, label_visibility="collapsed", key="router_menu")'
    if anchor in code and "Navegação rápida" not in code:
        inject = anchor + '''
# ===== Inline nav (fallback) — mantém abas visíveis mesmo sem sidebar
st.markdown("<div class='card' style='margin-top:8px'>", unsafe_allow_html=True)
_page_inline = st.radio("Navegação rápida", MENU, horizontal=True, index=MENU.index(page) if page in MENU else 0, key="router_menu_inline")
st.markdown("</div>", unsafe_allow_html=True)
if _page_inline != page:
    st.session_state["router_menu"] = _page_inline
    page = _page_inline
'''
        code = code.replace(anchor, inject, 1)
    return code

def main():
    app_path = HERE / "app.py"
    if not app_path.exists():
        app_path = HERE / "hab_os_app.py"
        if not app_path.exists():
            print("ERRO: Coloque este arquivo apply_patch.py na mesma pasta do seu app.py e rode novamente.")
            sys.exit(2)

    backup = app_path.with_suffix(".py.bak")
    # backup
    shutil.copy2(app_path, backup)

    code = read(app_path)
    code = patch_sidebar(code)
    code = patch_flash(code)
    code = patch_indexes(code)
    code = patch_inline_nav(code)

    out = app_path.with_name("app_patched.py")
    write(out, code)

    print("OK! Backup criado:", backup.name)
    print("Arquivo corrigido:", out.name)
    print("Renomeie app_patched.py para app.py e rode: streamlit run app.py")

if __name__ == "__main__":
    main()
