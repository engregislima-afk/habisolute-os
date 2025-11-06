# =========================================================
# PÁGINA EMITIR OS
# =========================================================
def page_emitir_os():
    st.markdown('<div class="section-title">Emitir OS</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    with SessionLocal() as sess:
        obras = sess.query(Obra).filter(Obra.ativo==1).order_by(Obra.nome.asc()).all()
        servicos = sess.query(Servico).filter(Servico.ativo==1).order_by(Servico.descricao.asc()).all()
        os_list = sess.query(OS).order_by(OS.id.desc()).limit(50).all()

    ops = ["(Nova OS)"] + [f"{o.id} — {o.numero} — {o.status}" for o in os_list]
    os_sel = st.selectbox("Selecione OS", ops, key="os_sel_emitir")
    if os_sel != "(Nova OS)":
        os_id = int(os_sel.split("—",1)[0].strip())
        with SessionLocal() as sess:
            os_db = sess.get(OS, os_id)
        novo = False
    else:
        os_db = None; novo = True

    obra_opc = [f"{o.id} — {o.nome}" for o in obras]
    if novo:
        obra_idx = 0; status_os = "Aberta"; obs = ""; dt_em = date.today()
    else:
        obra_idx = 0
        for i,o in enumerate(obras):
            if o.id == os_db.obra_id: obra_idx = i; break
        status_os = os_db.status; obs = os_db.observacoes or ""; dt_em = os_db.data_emissao or date.today()

    obra_sel = st.selectbox("Obra", obra_opc, index=obra_idx if obra_opc else 0)
    st.date_input("Data de emissão", value=dt_em, key="emit_os_dt")
    st.selectbox("Status", STATUS_OPTIONS, index=STATUS_OPTIONS.index(status_os) if status_os in STATUS_OPTIONS else 0, key="emit_os_status")
    os_obs = st.text_area("Observações", obs, height=110)

    st.markdown("Itens da OS")
    c1,c2,c3,c4 = st.columns([2.8,1,1,0.4])
    with c1:
        srv_ops = [f"{s.id} — {s.codigo} — {s.descricao}" for s in servicos]
        srv_sel = st.selectbox("Serviço", srv_ops, key="emit_os_srv")
    with c2:
        qtd = st.number_input("Qtd", min_value=0.0, value=1.0, step=1.0, format="%.2f")
    with c3:
        preco_in = st.number_input("Preço unit.", min_value=0.0, value=0.0, step=1.0, format="%.2f")
    with c4:
        st.write("")
        add_item = st.button("➕", key="btn_add_item_os")

    if st.button("Salvar OS", use_container_width=True):
        obra_id = int(obra_sel.split("—",1)[0].strip())
        with SessionLocal() as sess:
            if novo:
                num = gerar_numero_os(sess)
                nova = OS(numero=num, data_emissao=s["emit_os_dt"], obra_id=obra_id, status=s["emit_os_status"], observacoes=os_obs)
                sess.add(nova); sess.commit()
            else:
                obj = sess.get(OS, os_db.id)
                obj.data_emissao = s["emit_os_dt"]; obj.obra_id = obra_id; obj.status = s["emit_os_status"]; obj.observacoes = os_obs
                sess.commit()
        st.success("OS salva.")
        _rerun()

    if add_item and not novo:
        srv_id = int(srv_sel.split("—",1)[0].strip())
        obra_id = int(obra_sel.split("—",1)[0].strip())
        with SessionLocal() as sess:
            os_obj = sess.get(OS, os_db.id)
            ospec = sess.query(ObraServico).filter(ObraServico.obra_id==obra_id, ObraServico.servico_id==srv_id, ObraServico.ativo==1).first()
            sv = sess.get(Servico, srv_id)
            preco_final = preco_in or (ospec.preco_unit if ospec else (sv.preco_unit or 0.0))
            sess.add(OSItem(os_id=os_obj.id, servico_id=sv.id, quantidade_prevista=qtd, preco_unit=preco_final))
            sess.commit()
        st.success("Item incluído.")
        _rerun()

    if not novo:
        with SessionLocal() as sess:
            os_row, obra_row, itens = obter_os_com_itens(sess, os_db.id)
        if itens:
            df = pd.DataFrame(itens)
            df = df.rename(columns={"codigo":"Código","descricao":"Descrição","unidade":"Un","qtd_prev":"Qtd","preco_unit":"Preço","subtotal":"Subtotal"})
            st.dataframe(df, use_container_width=True)
        else:
            st.info("Esta OS ainda não tem itens.")
    st.markdown('</div>', unsafe_allow_html=True)

# =========================================================
# VISUALIZAR / IMPRIMIR
# =========================================================
def page_visualizar_imprimir():
    st.markdown('<div class="section-title">Visualizar / Imprimir</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    with SessionLocal() as sess:
        os_df = to_df(sess, OS)
        obras_map = {o.id: f"{o.nome} — {o.endereco}" for o in sess.query(Obra).all()}
    if os_df.empty:
        banner("info","Nenhuma OS emitida.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    os_df["data_emissao"] = pd.to_datetime(os_df["data_emissao"], errors="coerce").dt.date
    os_df["obra_nome"] = os_df["obra_id"].map(lambda oid: obras_map.get(oid, f"Obra {oid}"))
    os_df["data_str"] = os_df["data_emissao"].apply(lambda d: d.strftime("%d/%m/%Y") if isinstance(d,date) else "")
    f1,f2 = st.columns([2,1])
    ob_ops = ["(Todas)"] + sorted(os_df["obra_nome"].dropna().unique().tolist())
    ob_f = f1.selectbox("Filtrar por obra", ob_ops)
    st_f = f2.selectbox("Status", ["(Todos)"]+STATUS_OPTIONS)
    min_dt = os_df["data_emissao"].min() or date.today()
    max_dt = os_df["data_emissao"].max() or date.today()
    ini,fim = st.date_input("Período", value=(min_dt, max_dt))
    dfv = os_df.copy()
    if ob_f != "(Todas)": dfv = dfv[dfv["obra_nome"]==ob_f]
    if st_f != "(Todos)": dfv = dfv[dfv["status"]==st_f]
    dfv = dfv[(dfv["data_emissao"]>=ini) & (dfv["data_emissao"]<=fim)]
    q = st.text_input("Buscar por número da OS", "").strip()
    if q:
        dfv = dfv[dfv["numero"].str.contains(q, na=False, case=False)]
    if dfv.empty:
        banner("warn","Nenhuma OS encontrada com os filtros.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    dfv["label"] = dfv.apply(lambda r: f"{r['numero']} — {r['obra_nome']} — {r['data_str']} [{r['status']}]", axis=1)
    sel = st.selectbox("Selecione a OS", dfv["label"].tolist())
    row = dfv[dfv["label"]==sel].iloc[0]
    with SessionLocal() as sess:
        os_row_db = sess.get(OS, int(row["id"]))
        os_row, obra_row, itens = obter_os_com_itens(sess, os_row_db.id)
        cli = sess.get(Cliente, obra_row.cliente_id) if obra_row.cliente_id else None
    st.write(f"**OS:** {os_row.numero}")
    st.write(f"**Data:** {os_row.data_emissao.strftime('%d/%m/%Y')}")
    st.write(f"**Obra:** {obra_row.nome}")
    st.write(f"**Endereço:** {obra_row.endereco}")
    st.write(f"**Cliente:** {(cli.nome if cli else (obra_row.cliente or '-'))}")
    if os_row.observacoes:
        st.write(f"**Observações:** {os_row.observacoes}")
    tot = sum(it["subtotal"] for it in itens)
    st.markdown(f"<div class='card'><b>Total estimado</b><div style='font-size:1.4rem'>{format_brl(tot)}</div></div>", unsafe_allow_html=True)
    if itens:
        dfi = pd.DataFrame(itens).rename(columns={"codigo":"Código","descricao":"Descrição","unidade":"Un","qtd_prev":"Qtd","preco_unit":"Preço","subtotal":"Subtotal"})
        st.dataframe(dfi, use_container_width=True)
    sig = load_signature_bytes()
    pdf_interno = gerar_pdf_os(os_row, obra_row, itens, show_prices=True, logo_bytes=None, signature_bytes=sig)
    pdf_cliente = gerar_pdf_os(os_row, obra_row, itens, show_prices=False, logo_bytes=None, signature_bytes=sig)
    c1,c2 = st.columns(2)
    with c1:
        st.download_button("Baixar PDF (interno — com preços)", data=pdf_interno, file_name=f"{os_row.numero}_interno.pdf", mime="application/pdf")
    with c2:
        st.download_button("Baixar PDF (cliente — sem preços)", data=pdf_cliente, file_name=f"{os_row.numero}_cliente.pdf", mime="application/pdf")
    st.markdown('</div>', unsafe_allow_html=True)

# =========================================================
# MEDIÇÃO
# =========================================================
def page_medicao():
    st.markdown('<div class="section-title">Medição Mensal</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    with SessionLocal() as sess:
        obras = sess.query(Obra).order_by(Obra.nome.asc()).all()
    if not obras:
        banner("info","Cadastre obras primeiro.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    obra_ops = [f"{o.id} — {o.nome}" for o in obras]
    obra_sel = st.selectbox("Obra", obra_ops)
    obra_id = int(obra_sel.split("—",1)[0].strip())
    ini,fim = st.date_input("Período da medição", value=(date.today().replace(day=1), date.today()))
    linhas = []
    with SessionLocal() as sess:
        os_obra = sess.query(OS).filter(OS.obra_id==obra_id, OS.data_emissao>=ini, OS.data_emissao<=fim).order_by(OS.data_emissao.asc()).all()
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
        df = pd.DataFrame(linhas)
        st.dataframe(df, use_container_width=True)
        sig = load_signature_bytes()
        pdf = gerar_pdf_medicao(
            obra_nome=obra_sel.split("—",1)[1].strip(),
            periodo_str=f"{ini.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}",
            linhas=linhas,
            logo_bytes=None,
            medicao_num=1,
            signature_bytes=sig,
        )
        st.download_button("Baixar PDF da medição", data=pdf, file_name=f"medicao_{obra_id}_{ini:%Y%m}.pdf", mime="application/pdf")
    else:
        st.info("Nenhuma OS dessa obra no período.")
    st.markdown('</div>', unsafe_allow_html=True)

# =========================================================
# RELATÓRIOS
# =========================================================
def gerar_pdf_fechamento(cliente_nome: str, periodo_str: str, linhas: list[dict], logo_bytes: bytes | None, signature_bytes: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=28, bottomMargin=40, leftMargin=14, rightMargin=14)
    story = []
    story += _header_vertical_centralizado()
    info_tbl = Table([[Paragraph(f"<b>Cliente:</b> {cliente_nome}", styleSmall)],
                      [Paragraph(f"<b>Período:</b> {periodo_str}", styleSmall)]], colWidths=[doc.width])
    info_tbl.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.6,colors.black)]))
    story += [info_tbl, Spacer(1,6)]
    titulo = "FECHAMENTO POR CLIENTE"
    tit_tbl = Table([[Paragraph(f"<b>{titulo}</b>", ParagraphStyle("t", parent=styleN, fontSize=11, alignment=TA_CENTER))]], colWidths=[doc.width])
    tit_tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")),("BOX",(0,0),(-1,-1),0.5,colors.black)]))
    story += [tit_tbl, Spacer(1,6)]
    agreg = {}
    for r in linhas:
        key = (r.get("obra") or "-", r["codigo"], r["descricao"], r["un"])
        acc = agreg.setdefault(key, {"qtd":0.0,"val":0.0})
        acc["qtd"] += float(r.get("qtd",0.0) or 0.0)
        acc["val"] += float(r.get("subtotal",0.0) or 0.0)
    rows = [["Obra","Código","Descrição","Un","Qtd","Subtotal"]]
    total = 0.0
    for (obra,cod,desc,un),acc in sorted(agreg.items(), key=lambda x:(x[0][0],x[0][1])):
        rows.append([obra,cod,desc,un,f"{acc['qtd']:.2f}",format_brl(acc["val"])])
        total += acc["val"]
    W = doc.width
    tbl = Table(rows, colWidths=[0.28*W,0.10*W,0.34*W,0.06*W,0.10*W,0.12*W], repeatRows=1)
    tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.black),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),0.25,colors.black)]))
    story.append(tbl); story.append(Spacer(1,6))
    tot_box = Table([[Paragraph("<b>Total geral:</b>", styleN), Paragraph(f"<b>{format_brl(total)}</b>", styleN)]], colWidths=[36*mm,42*mm])
    tot_box.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.75,colors.black),("ALIGN",(1,0),(1,0),"RIGHT")]))
    wrap = Table([[None, tot_box]], colWidths=[doc.width-(36*mm+42*mm), (36*mm+42*mm)])
    story.append(wrap)
    doc.build(story, onFirstPage=lambda c,d:_on_page(c,d,titulo), onLaterPages=lambda c,d:_on_page(c,d,titulo))
    return buf.getvalue()

def page_relatorios():
    st.markdown('<div class="section-title">Relatórios</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    with SessionLocal() as sess:
        clientes = sess.query(Cliente).order_by(Cliente.nome.asc()).all()
    if not clientes:
        banner("info","Cadastre clientes primeiro.")
        st.markdown('</div>', unsafe_allow_html=True)
        return
    cli_ops = [f"{c.id} — {c.nome}" for c in clientes]
    cli_sel = st.selectbox("Cliente", cli_ops)
    cli_id = int(cli_sel.split("—",1)[0].strip())
    ini,fim = st.date_input("Período", value=(date.today().replace(day=1), date.today()))
    linhas = []
    with SessionLocal() as sess:
        obras_cli = sess.query(Obra).filter(Obra.cliente_id==cli_id).all()
        ids = [o.id for o in obras_cli]
        os_rows = sess.query(OS).filter(OS.obra_id.in_(ids), OS.data_emissao>=ini, OS.data_emissao<=fim).order_by(OS.data_emissao.asc()).all()
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
        df = pd.DataFrame(linhas); st.dataframe(df, use_container_width=True)
        pdf = gerar_pdf_fechamento(
            cliente_nome=cli_sel.split("—",1)[1].strip(),
            periodo_str=f"{ini.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}",
            linhas=linhas,
            logo_bytes=None,
            signature_bytes=load_signature_bytes(),
        )
        st.download_button("Baixar PDF de fechamento", data=pdf, file_name=f"fechamento_{cli_id}_{ini:%Y%m}.pdf", mime="application/pdf")
    else:
        st.info("Nada a mostrar nesse período.")
    st.markdown('</div>', unsafe_allow_html=True)

# =========================================================
# EXPORTAÇÃO
# =========================================================
def make_os_excel_per_obras() -> tuple[bytes,str,str]:
    with SessionLocal() as sess:
        os_rows = sess.query(OS).order_by(OS.data_emissao.desc()).all()
        obras = {o.id:o for o in sess.query(Obra).all()}
        clientes = {c.id:c for c in sess.query(Cliente).all()}
    data = []
    for o in os_rows:
        ob = obras.get(o.obra_id)
        cli = clientes.get(ob.cliente_id) if ob and ob.cliente_id else None
        data.append({
            "OS": o.numero,
            "Data emissão": o.data_emissao.strftime("%d/%m/%Y") if o.data_emissao else "",
            "Status": o.status,
            "Obra": ob.nome if ob else "",
            "Endereço": ob.endereco if ob else "",
            "Cliente": cli.nome if cli else (ob.cliente if ob else ""),
        })
    df = pd.DataFrame(data)
    try:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name="OS")
        return output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "os_por_obras.xlsx"
    except Exception:
        return df.to_csv(index=False).encode("utf-8-sig"), "text/csv", "os_por_obras.csv"

def page_export():
    st.markdown('<div class="section-title">Exportações</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    with st.expander("Backup (DB + anexos)"):
        if st.button("Gerar backup ZIP", key="btn_backup_zip"):
            p = make_full_backup()
            st.download_button("Baixar backup", data=p.read_bytes(), file_name=p.name, mime="application/zip")
    with st.expander("Exportar OS por obra"):
        data, mime, fname = make_os_excel_per_obras()
        st.download_button("Baixar planilha", data=data, file_name=fname, mime=mime)
    with st.expander("Assinatura digital (PDF)"):
        st.write("Envie a imagem da assinatura")
        up = st.file_uploader("Assinatura", type=["png","jpg","jpeg"])
        if up is not None:
            if save_signature_file(up):
                banner("success","Assinatura salva.")
        sig = load_signature_bytes()
        if sig:
            st.image(sig, caption="Assinatura atual", width=180)
    st.markdown('</div>', unsafe_allow_html=True)

# =========================================================
# MENU / ROUTER
# =========================================================
st.sidebar.markdown("### Sistema OS", unsafe_allow_html=True)
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
