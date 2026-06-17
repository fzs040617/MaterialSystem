import streamlit as st
import pandas as pd
import datetime
import os,re
import numpy as np
import threading
from sync_history_to_feishu import migrate_accessory_history_to_feishu
from database import get_db_conn, load_data
from excel_engines import generate_accessory_excel
from rpa_bridge import load_accessory_rpa_result, open_file_for_streamlit_upload

def _safe_accessory_filename_part(value):
    """清理辅料下单下载文件名片段"""
    text = "" if value is None else str(value).strip()

    # 去掉 Windows 文件名不允许字符
    text = re.sub(r'[\\/:*?"<>|]+', '', text)

    # 去掉括号、加号、空白，让款式/工厂名更干净
    text = text.replace("+", "")
    text = re.sub(r'[（）()]', '', text)
    text = re.sub(r'\s+', '', text)

    return text or "未填写"


def _format_accessory_output_filename(internal_code, accessory_type, selected_factory_name, order_date):
    """辅料下单表下载文件名：内部码 款式名称 工厂名称 日期.xlsx"""
    code_part = _safe_accessory_filename_part(internal_code)
    style_part = _safe_accessory_filename_part(accessory_type)
    factory_part = _safe_accessory_filename_part(selected_factory_name)

    if hasattr(order_date, "strftime"):
        date_part = order_date.strftime("%Y%m%d")
    else:
        raw_date = _safe_accessory_filename_part(order_date)
        digits = re.sub(r'\D+', '', raw_date)
        date_part = digits if len(digits) == 8 else raw_date

    return f"{code_part} {style_part} {factory_part} {date_part}.xlsx"


def _generate_accessory_from_source(source_file, params, uname):
    excel_bytes, missing_69_count = generate_accessory_excel(source_file, params)

    download_filename = _format_accessory_output_filename(
        internal_code=params.get("internal_code", ""),
        accessory_type=params.get("accessory_type", ""),
        selected_factory_name=params.get("selected_factory_name", ""),
        order_date=params.get("order_date", datetime.date.today())
    )

    params["output_filename"] = download_filename

    if hasattr(source_file, "seek"):
        source_file.seek(0)
    save_accessory_history(source_file, params, excel_bytes, uname)

    return excel_bytes, missing_69_count, download_filename

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
        wash_material = st.radio("洗水唛材料", ["胶带（唯品/三野）", "布带（天猫/抖音）"], horizontal=True) if has_wash == "有" else None
    with c3:
        st.markdown("**辅料款式 (必选)**")
        accessory_type = st.radio("款式", ["绿色吊牌+吊粒", "五张新吊牌+防伪带", "贴纸"], horizontal=True, label_visibility="collapsed")

    c4, c5 = st.columns(2)
    internal_code = c4.text_input("采购单查询码 (必填)", placeholder="例如: CG260206012")
    material_text = c5.text_input("材质表 (选填)", placeholder="例如: 锦纶79.5% 氨纶20.5%")
    is_two_pack = st.radio("是否两件装", ["否", "是"], horizontal=True, index=0)

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
            raw_addr = f"{fac_info.get('address', '')}".strip()
            selected_factory_addr = f"{selected_factory_name}：{raw_addr}" if raw_addr else selected_factory_name
            st.success(f"📍 **收件信息:** {selected_factory_addr}")

    # 4. 文件上传与生成引擎调用
    st.subheader("📤 3. 上传基础表并生成")
    uploaded_wdt = st.file_uploader("上传旺店通表格 (.xls / .xlsx / .csv)", type=['xls', 'xlsx', 'csv'])

    st.subheader("🤖 RPA 结果生成（测试）")
    st.info("请先运行影刀 RPA，让它把旺店通原表下载到固定目录并写入 result.json。09:00-10:00、14:00-15:00 不建议运行 RPA。手动上传入口仍保留作为兜底。")
    rpa_result_btn = st.button("读取 RPA 结果并生成辅料下单表", use_container_width=True)

    if st.button("🚀 开始生成辅料下单表", type="primary", use_container_width=True):
        if not uploaded_wdt:
            st.error("❌ 请先上传表格！")
        elif not internal_code.strip():
            st.error("请先填写采购单查询码")
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
                        'is_two_pack': is_two_pack,
                        'accessory_type': accessory_type,
                        'internal_code': internal_code,
                        'material_text': material_text,
                        'selected_factory_addr': selected_factory_addr,
                        'selected_factory_name': selected_factory_name
                    }
                    excel_bytes, missing_69_count, download_filename = _generate_accessory_from_source(
                        uploaded_wdt, params, uname
                    )

                    st.success("🎉 生成成功并已自动归档！")
                    if has_69 == '有' and missing_69_count > 0:
                        st.warning(f"⚠️ 提示：有 {missing_69_count} 个条码未匹配到 69 码。")

                    st.download_button(
                        label="📥 下载辅料下单表",
                        data=excel_bytes,
                        file_name=download_filename,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        type="primary"
                    )
                except Exception as e:
                    st.error(f"处理失败: {e}")

    if rpa_result_btn:
        if not internal_code.strip():
            st.error("请先填写采购单查询码")
        elif not selected_factory_name:
            st.error("❌ 请勾选收货制衣厂！")
        else:
            with st.spinner("🤖 正在读取 RPA 结果..."):
                try:
                    rpa_result = load_accessory_rpa_result(expected_internal_code=internal_code.strip())
                    if not rpa_result["ok"]:
                        st.error(rpa_result["message"])
                    else:
                        file_obj = open_file_for_streamlit_upload(rpa_result["file_path"])
                        params = {
                            'order_date': acc_order_date,
                            'has_69': has_69,
                            'has_wash': has_wash,
                            'wash_material': wash_material,
                            'is_two_pack': is_two_pack,
                            'accessory_type': accessory_type,
                            'internal_code': internal_code,
                            'material_text': material_text,
                            'selected_factory_addr': selected_factory_addr,
                            'selected_factory_name': selected_factory_name
                        }
                        excel_bytes, missing_69_count, download_filename = _generate_accessory_from_source(
                            file_obj, params, uname
                        )
                        st.success(f"🎉 {rpa_result['message']}")
                        if has_69 == '有' and missing_69_count > 0:
                            st.warning(f"⚠️ 提示：有 {missing_69_count} 个条码未匹配到 69 码。")
                        st.download_button(
                            label="📥 下载辅料下单表",
                            data=excel_bytes,
                            file_name=download_filename,
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            type="primary",
                            key="download_rpa_accessory"
                        )
                except Exception as e:
                    st.error(f"读取 RPA 结果失败: {e}")

def save_accessory_history(uploaded_file, params, excel_bytes, uname):
    """【内部逻辑】辅助函数：解析上传文件并保存至数据库历史"""
    try:
        uploaded_file.seek(0)
        file_ext = os.path.splitext(uploaded_file.name)[1].lower()
        df_tmp = pd.read_csv(uploaded_file) if file_ext == '.csv' else pd.read_excel(uploaded_file)
        df_tmp.columns = [str(col).strip() for col in df_tmp.columns]

        # 简单提取摘要信息
        item_no_val = "未知货号"
        p_name_val = "未知产品"
        total_q_val = 0

        # 过滤旺店通原表自带的合计 / 总计 / 小计行，避免历史数量重复
        summary_keywords = r'合计|总计|小计'
        summary_mask = pd.Series(False, index=df_tmp.index)
        for col in df_tmp.columns:
            summary_mask = summary_mask | df_tmp[col].fillna('').astype(str).str.strip().str.contains(
                summary_keywords,
                regex=True,
                na=False
            )
        df_detail = df_tmp[~summary_mask].copy()

        # 优先从“货品编号”提取货号
        if '货品编号' in df_detail.columns:
            valid_item_no = df_detail['货品编号'].dropna().astype(str).str.strip()
            valid_item_no = valid_item_no[valid_item_no != '']
            if not valid_item_no.empty:
                item_no_val = valid_item_no.iloc[0]
        else:
            # 兼容旧表：智能匹配 R 开头货号
            for col in df_detail.columns:
                col_text = df_detail[col].astype(str)
                matched = df_detail[col_text.str.match(r'^R[A-Z]\d+', na=False)]
                if not matched.empty:
                    item_no_val = str(matched[col].iloc[0])
                    break

        # 优先从“货品名称”提取产品名，并删除 VIP
        if '货品名称' in df_detail.columns:
            valid_names = df_detail['货品名称'].dropna().astype(str).str.strip()
            valid_names = valid_names[valid_names != '']
            if not valid_names.empty:
                p_name_val = re.sub(r'\(VIP\)|（VIP）', '', valid_names.iloc[0]).strip()
        else:
            name_cols = [c for c in df_detail.columns if '名称' in c]
            if name_cols:
                p_name_val = str(df_detail[name_cols[0]].iloc[0])
                p_name_val = re.sub(r'\(VIP\)|（VIP）', '', p_name_val).strip()

        # 历史记录数量：优先使用“采购确认量”，只统计真实明细，不再乘 1.02
        if '采购确认量' in df_detail.columns:
            qty_series = pd.to_numeric(df_detail['采购确认量'], errors='coerce').dropna()
            total_q_val = int(qty_series.sum()) if not qty_series.empty else 0
        else:
            # 兼容旧表：找包含“量”的列，但不额外乘 1.02
            qty_cols = [c for c in df_detail.columns if '量' in c]
            if qty_cols:
                qty_series = pd.to_numeric(df_detail[qty_cols[0]], errors='coerce').dropna()
                total_q_val = int(qty_series.sum()) if not qty_series.empty else 0

        # 写入数据库
        conn = get_db_conn()
        c = conn.cursor()
        create_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        c.execute('''INSERT INTO accessory_history 
                     (create_time, order_date, operator, factory_name, file_name, excel_data,
                      item_no, product_name, acc_style, total_qty, internal_code, material_info) 
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                  (create_time, str(params['order_date']), uname, params['selected_factory_name'],
                   params.get('output_filename', f"辅料单_{params['selected_factory_name']}.xlsx"), excel_bytes,
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
