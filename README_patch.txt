Habisolute — Sistema de OS (Streamlit)
PATCH KIT — Corrige abas sumidas e erro de índices no SQLite

O que este patch faz:
1) Deixa a sidebar sempre expandida.
2) Conserta a função flash() que estava chamando função inexistente.
3) Adiciona um menu "Navegação rápida" horizontal no topo (sincronizado com a sidebar).
4) Cria os índices do SQLite com checagem e try/except (sem quebrar no Streamlit Cloud).

Como aplicar:
1) Salve este README e o script abaixo (apply_patch.py) na MESMA pasta do seu app atual (onde está o app.py).
2) Rode:  python apply_patch.py
   - Ele cria um backup: app.py.bak
   - E gera o arquivo corrigido: app_patched.py
3) Substitua o app.py pelo app_patched.py (ou renomeie) e rode:
   streamlit run app.py

Login inicial: admin / 1234 (o app pedirá troca).
