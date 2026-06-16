import streamlit as st
import pandas as pd
import datetime
import os
import numpy as np
import threading
from sync_history_to_feishu import migrate_accessory_history_to_feishu
from database import get_db_conn, load_data
from excel_engines import generate_accessory_excel

def render_accessory_order(uname):
    """渲染辅料下单界面"""
    st.header("🖨️ 辅料下单表自动生成")
    st.caption("上传旺店通基础表，一键匹配69码并生成辅料厂标准下单格式。")
    
    # 1. 订单元数据配置
    c_meta = st.columns(2)
    acc_order_date = c_meta[0].date_input("下单日期", datetime.date.today(), key="acc_order_date")
    st.divider()

    # 2. 基础逻辑配置
    st.subheader("⚙️ 1. 基础配置")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**国际条码 (69码)**")
        has_69 = st.radio("是否有69码", ["无", "有"], horizontal=True, label_visibility="collapsed")
    with c2:
        st.markdown("**洗水唛**")
        has_wash = st.radio("是否有洗水唛", ["无", "有"], horizontal=True, label_visibility="collapsed")
        wash_material = st.radio("材质", ["布质", "胶质"], horizontal=True) if has_wash == "有" else None
    with c3:
        st.markdown("**辅料款式 (必选)**")
        accessory_type = st.radio("款式", ["吊牌+吊粒", "吊牌+防伪带", "贴纸"], horizontal=True, label_visibility="collapsed")

    c4, c5 = st.columns(2)
    internal_code = c4.text_input("内部码 (选填)", placeholder="例如: CG260206012")
    material_text = c5.text_input("材质表 (选填)", placeholder="例如: 锦纶79.5% 氨纶20.5%")

    # 3. 制衣厂选择逻辑
    st.subheader("🏭 2. 选择收货制衣厂")
    df_g = load_data("garment_factories")
    selected_factory_addr = ""
    selected_factory_name = ""
    
    if df_g.empty:
        st.warning("⚠️ 请先去后台添加制衣厂数据")
    else:
        factory_list = df_g['name'].tolist()
        quick_search_acc = st.multiselect("🔍 快速搜索", options=factory_list, key="search_fac_accessory")
        
        df_display_acc = df_g[['name']].copy()
        df_display_acc.insert(0, "✅", df_display_acc['name'].isin(quick_search_acc))

        edited_gf = st.data_editor(
            df_display_acc,
            key="editor_accessory",
            column_config={"✅": st.column_config.CheckboxColumn("选", width="small")},
            hide_index=True,
            use_container_width=True,
            height=200
        )
        
        selected_rows = edited_gf[edited_gf["✅"] == True]
        if not selected_rows.empty:
            selected_factory_name = selected_rows.iloc[0]['name']
            fac_info = df_g[df_g['name'] == selected_factory_name].iloc[0]
            selected_factory_addr = f"{fac_info.get('address', '')}".strip()
            st.success(f"📍 **收件信息:** {selected_factory_addr}")

    # 4. 文件上传与生成引擎调用
    st.subheader("📤 3. 上传基础表并生成")
    uploaded_wdt = st.file_uploader("上传旺店通表格 (.xls / .xlsx / .csv)", type=['xls', 'xlsx', 'csv'])

    if st.button("🚀 开始生成辅料下单表", type="primary", use_container_width=True):
        if not uploaded_wdt:
            st.error("❌ 请先上传表格！")
        elif not selected_factory_name:
            st.error("❌ 请勾选收货制衣厂！")
        else:
            with st.spinner("⚙️ 正在执行数据转化..."):
                try:
                    params = {
                        'order_date': acc_order_date,
                        'has_69': has_69,
                        'has_wash': has_wash,
                        'wash_material': wash_material,
                        'accessory_type': accessory_type,
                        'internal_code': internal_code,
                        'material_text': material_text,
                        'selected_factory_addr': selected_factory_addr,
                        'selected_factory_name': selected_factory_name
                    }
                    
                    # 调用 Excel 引擎
                    excel_bytes, missing_69_count = generate_accessory_excel(uploaded_wdt, params)
                    
                    # 5. 自动归档历史记录
                    save_accessory_history(uploaded_wdt, params, excel_bytes, uname)

                    st.success("🎉 生成成功并已自动归档！")
                    if has_69 == '有' and missing_69_count > 0:
                        st.warning(f"⚠️ 提示：有 {missing_69_count} 个条码未匹配到 69 码。")
                        
                    st.download_button("📥 下载辅料下单表", excel_bytes, f"辅料单_{selected_factory_name}.xlsx", type="primary")
                except Exception as e:
                    st.error(f"处理失败: {e}")

def save_accessory_history(uploaded_file, params, excel_bytes, uname):
    """【内部逻辑】辅助函数：解析上传文件并保存至数据库历史"""
    try:
        uploaded_file.seek(0)
        file_ext = os.path.splitext(uploaded_file.name)[1].lower()
        df_tmp = pd.read_csv(uploaded_file) if file_ext == '.csv' else pd.read_excel(uploaded_file)
        
        # 简单提取摘要信息 (货号、产品名等)
        item_no_val = "未知货号"
        p_name_val = "未知产品"
        total_q_val = 0
        
        # 智能匹配列名并提取
        for col in df_tmp.columns:
            if df_tmp[col].astype(str).str.match(r'^R[A-Z]\d+').any():
                item_no_val = df_tmp[df_tmp[col].astype(str).str.match(r'^R[A-Z]\d+')][col].iloc[0]
                break
        
        name_cols = [c for c in df_tmp.columns if '名称' in c]
        if name_cols: p_name_val = str(df_tmp[name_cols[0]].iloc[0])

        qty_cols = [c for c in df_tmp.columns if '量' in c]
        if qty_cols:
            raw_qty = pd.to_numeric(df_tmp[qty_cols[0]], errors='coerce').sum()
            total_q_val = int(np.ceil(raw_qty * 1.02))

        # 写入数据库
        conn = get_db_conn(); c = conn.cursor()
        create_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute('''INSERT INTO accessory_history 
                     (create_time, order_date, operator, factory_name, file_name, excel_data,
                      item_no, product_name, acc_style, total_qty, internal_code, material_info) 
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''', 
                  (create_time, str(params['order_date']), uname, params['selected_factory_name'], 
                   f"辅料单_{params['selected_factory_name']}.xlsx", excel_bytes, 
                   item_no_val, p_name_val, params['accessory_type'], total_q_val, 
                   params['internal_code'], params['material_text']))
        conn.commit()
                    # 辅料下单成功后，异步同步到飞书多维表格
        try:
            sync_thread = threading.Thread(target=migrate_accessory_history_to_feishu)
            sync_thread.start()
        except Exception as e:
            print(f"后台触发辅料飞书同步失败: {e}")
        conn.close()
    except Exception:
        pass