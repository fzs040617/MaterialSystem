import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import datetime
import os,re
import numpy as np
import threading
from sync_history_to_feishu import migrate_accessory_history_to_feishu
from database import get_db_conn, load_data
from excel_engines import generate_accessory_excel
from rpa_bridge import load_accessory_rpa_result, open_file_for_streamlit_upload, write_accessory_order_rpa_request, load_accessory_order_rpa_status, start_accessory_order_rpa

def _safe_accessory_filename_part(value, remove_spaces=False):
    """清理辅料下单下载文件名片段"""
    text = "" if value is None else str(value).strip()

    # 去掉 Windows 文件名不允许字符
    text = re.sub(r'[\\/:*?"<>|]+', '', text)

    # 去掉加号和括号；多个空白合并
    text = text.replace("+", "")
    text = re.sub(r'[（）()]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()

    if remove_spaces:
        text = re.sub(r'\s+', '', text)

    return text or "未填写"

def _format_accessory_output_filename(internal_code, accessory_type, product_name, selected_factory_name, order_date):
    """辅料下单表下载文件名：内部码 吊牌款式 款式名称工厂名字 日期.xlsx"""
    code_part = _safe_accessory_filename_part(internal_code, remove_spaces=True)
    style_part = _safe_accessory_filename_part(accessory_type, remove_spaces=True)
    product_part = _safe_accessory_filename_part(product_name, remove_spaces=False)
    factory_part = _safe_accessory_filename_part(selected_factory_name, remove_spaces=True)

    if hasattr(order_date, "strftime"):
        date_part = order_date.strftime("%Y%m%d")
    else:
        raw_date = _safe_accessory_filename_part(order_date, remove_spaces=True)
        digits = re.sub(r'\D+', '', raw_date)
        date_part = digits if len(digits) == 8 else raw_date

    # 款式名称和工厂名按需求连在一起；吊牌款式中的“+”已被清理
    if product_part and product_part != "未填写":
        name_part = f"{product_part}{factory_part}"
    else:
        name_part = factory_part

    return f"{code_part} {style_part} {name_part} {date_part}.xlsx"

def _extract_accessory_product_name_from_source(source_file):
    """从旺店通原表中提取款式名称，用于辅料下单表文件名"""
    try:
        if hasattr(source_file, "seek"):
            source_file.seek(0)

        file_name = getattr(source_file, "name", "")
        file_ext = os.path.splitext(file_name)[1].lower()

        if file_ext == ".csv":
            df_tmp = pd.read_csv(source_file)
        else:
            df_tmp = pd.read_excel(source_file)

        df_tmp.columns = [str(col).strip() for col in df_tmp.columns]

        if "货品名称" not in df_tmp.columns:
            return ""

        summary_keywords = r'合计|总计|小计'
        summary_mask = pd.Series(False, index=df_tmp.index)

        for col in df_tmp.columns:
            summary_mask = summary_mask | df_tmp[col].fillna('').astype(str).str.strip().str.contains(
                summary_keywords,
                regex=True,
                na=False
            )

        df_detail = df_tmp[~summary_mask].copy()
        names = df_detail["货品名称"].dropna().astype(str).str.strip()
        names = names[names != ""]

        if names.empty:
            return ""

        product_name = names.iloc[0]
        product_name = re.sub(r'\(VIP\)|（VIP）', '', product_name).strip()
        return product_name

    except Exception:
        return ""
    finally:
        try:
            if hasattr(source_file, "seek"):
                source_file.seek(0)
        except Exception:
            pass

def _generate_accessory_from_source(source_file, params, uname):
    excel_bytes, missing_69_count = generate_accessory_excel(source_file, params)

    product_name = _extract_accessory_product_name_from_source(source_file)

    download_filename = _format_accessory_output_filename(
        internal_code=params.get("internal_code", ""),
        accessory_type=params.get("accessory_type", ""),
        product_name=product_name,
        selected_factory_name=params.get("selected_factory_name", ""),
        order_date=params.get("order_date", datetime.date.today())
    )

    params["output_filename"] = download_filename

    if hasattr(source_file, "seek"):
        source_file.seek(0)
    save_accessory_history(source_file, params, excel_bytes, uname)

    return excel_bytes, missing_69_count, download_filename

def _reset_accessory_form_state():
    """下载辅料下单表后，重置辅料下单页面，避免下一张单沿用上一张信息"""
    fixed_keys = [
        "acc_order_date",
        "acc_has_69",
        "acc_has_wash",
        "acc_wash_material",
        "acc_accessory_type",
        "acc_internal_code",
        "acc_material_text",
        "acc_is_two_pack",
    ]

    for key in fixed_keys:
        if key in st.session_state:
            del st.session_state[key]

    # 这些组件用动态 key，递增后会强制刷新为空
    st.session_state["acc_form_reset_token"] = st.session_state.get("acc_form_reset_token", 0) + 1

def _is_accessory_rpa_forbidden_now():
    """辅料下单 RPA 禁用时间段：09:00-10:00、14:00-15:00"""
    now = datetime.datetime.now()
    current_time = now.time()

    forbidden_ranges = [
        (datetime.time(9, 0), datetime.time(10, 0)),
        (datetime.time(14, 0), datetime.time(15, 0)),
    ]

    all_ranges_text = "09:00-10:00、14:00-15:00"

    for start_time, end_time in forbidden_ranges:
        if start_time <= current_time < end_time:
            return True, (
                f"当前时间 {now.strftime('%H:%M')} 处于 RPA 禁用时间段。"
                f"辅料下单 RPA 禁用时间段为：{all_ranges_text}。"
                "请在禁用时间段结束后再发起辅料下单 RPA 查询。"
            )

    return False, ""


def _show_accessory_rpa_time_notice():
    """显示辅料下单 RPA 时间段提示"""
    forbidden, message = _is_accessory_rpa_forbidden_now()
    if forbidden:
        st.error(f"⛔ {message}")
    else:
        st.caption("RPA 禁用时间段：09:00-10:00、14:00-15:00；其余时间可发起查询。")        
    return forbidden

def _compact_choice(label, options, key, default=None):
    """紧凑按钮式选择；如果当前 Streamlit 不支持 segmented_control，则自动退回 radio"""
    if default is None or default not in options:
        default = options[0]

    if hasattr(st, "segmented_control"):
        value = st.segmented_control(
            label,
            options,
            default=default,
            key=key
        )
        return value or default

    return st.radio(
        label,
        options,
        horizontal=True,
        key=key
    )

def render_accessory_order(uname):
    """渲染辅料下单界面"""
    st.header("🖨️ 辅料下单表自动生成")
    st.caption("上传旺店通基础表，一键匹配69码并生成辅料厂标准下单格式。")
    reset_token = st.session_state.get("acc_form_reset_token", 0)
    
    # 1. 订单元数据配置
    c_meta = st.columns(2)
    acc_order_date = c_meta[0].date_input("下单日期", datetime.date.today(), key="acc_order_date")
    st.divider()

    # 2. 基础逻辑配置
    st.subheader("⚙️ 1. 基础配置")

    # 第一行：紧凑按钮式选择，减少横向空白
    c1, c2, c3, c4 = st.columns([0.9, 0.9, 1.8, 0.9])

    with c1:
        has_69 = _compact_choice(
            "69码",
            ["无", "有"],
            key="acc_has_69",
            default="无"
        )

    with c2:
        has_wash = _compact_choice(
            "洗水唛",
            ["无", "有"],
            key="acc_has_wash",
            default="无"
        )

    with c3:
        accessory_type = _compact_choice(
            "辅料款式",
            ["绿色吊牌+吊粒", "五张新吊牌+防伪带", "贴纸"],
            key="acc_accessory_type",
            default="绿色吊牌+吊粒"
        )

    with c4:
        is_two_pack = _compact_choice(
            "两件装",
            ["否", "是"],
            key="acc_is_two_pack",
            default="否"
        )

    # 第二行：采购单查询码 + 洗水唛相关输入
    c5, c6 = st.columns([1.25, 1])

    internal_code = c5.text_input(
        "采购单查询码（必填）",
        placeholder="例如: CG260206012",
        key="acc_internal_code"
    )

    if has_wash == "有":
        wash_material = _compact_choice(
            "洗水唛材料",
            ["胶带（唯品/三野）", "布带（天猫/抖音）"],
            key="acc_wash_material",
            default="胶带（唯品/三野）"
        )

        material_text = c6.text_area(
            "洗水唛材质表（选填，最多4行）",
            placeholder="例如：\n锦纶79.5%\n氨纶20.5%",
            height=80,
            key="acc_material_text"
        )
    else:
        wash_material = None
        material_text = ""
    # 3. 制衣厂选择逻辑
    st.subheader("🏭 2. 选择收货制衣厂")
    df_g = load_data("garment_factories")
    selected_factory_addr = ""
    selected_factory_name = ""
    
    if df_g.empty:
        st.warning("⚠️ 请先去后台添加制衣厂数据")
    else:
        factory_list = df_g['name'].tolist()
        quick_search_acc = st.multiselect(
            "🔍 快速搜索",
            options=factory_list,
            key=f"search_fac_accessory_{reset_token}"
        )        
        df_display_acc = df_g[['name']].copy()
        df_display_acc.insert(0, "✅", df_display_acc['name'].isin(quick_search_acc))

        edited_gf = st.data_editor(
            df_display_acc,
            key=f"editor_accessory_{reset_token}",
            column_config={"✅": st.column_config.CheckboxColumn("选", width="small")},
            hide_index=True,
            use_container_width=True,
            height=145
        )
        
        selected_rows = edited_gf[edited_gf["✅"] == True]
        if not selected_rows.empty:
            selected_factory_name = selected_rows.iloc[0]['name']
            fac_info = df_g[df_g['name'] == selected_factory_name].iloc[0]
            raw_addr = f"{fac_info.get('address', '')}".strip()
            selected_factory_addr = f"{selected_factory_name}：{raw_addr}" if raw_addr else selected_factory_name
            st.success(f"📍 **收件信息:** {selected_factory_addr}")

    # 4. 文件上传与生成引擎调用：压缩为 Tab，减少页面上下滚动
    manual_tab, rpa_tab = st.tabs(["📤 上传生成", "🤖 RPA结果生成"])

    with manual_tab:
        uploaded_wdt = st.file_uploader(
            "上传旺店通表格 (.xls / .xlsx / .csv)",
            type=['xls', 'xlsx', 'csv'],
            key=f"uploaded_wdt_accessory_{reset_token}"
        )

        if st.button("🚀 开始生成辅料下单表", type="primary", use_container_width=True, key="btn_manual_accessory"):
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
                            type="primary",
                            key="download_manual_accessory",
                            on_click=_reset_accessory_form_state
                        )
                    except Exception as e:
                        st.error(f"处理失败: {e}")

    with rpa_tab:
        st.info(
            "半自动 RPA 流程：先填写采购单查询码 → 点击生成 RPA 请求文件 → 手动运行影刀 RPA → 再点击读取 RPA 结果生成辅料下单表。"
        )

        rpa_time_forbidden = _show_accessory_rpa_time_notice()

        def show_rpa_status(status_box, rpa_status):
            """只刷新状态显示区域，不刷新整个浏览器页面"""
            with status_box.container():
                if rpa_status.get("exists"):
                    status_value = str(rpa_status.get("status", "")).strip()
                    status_message = str(rpa_status.get("message", "")).strip()
                    status_code = str(rpa_status.get("internal_code", "")).strip()
                    status_time = str(rpa_status.get("updated_at", "")).strip()

                    if status_value == "pending":
                        st.warning(f"⏳ {status_message or 'RPA 请求文件已生成，请手动运行影刀 RPA'}")
                    elif status_value == "running":
                        st.info(f"🔄 {status_message or 'RPA 正在运行，请等待'}")
                    elif status_value == "success":
                        st.success(f"✅ {status_message or '查询成功，旺店通原表已下载完成'}")
                    elif status_value == "failed":
                        st.error(f"❌ {status_message or 'RPA 查询失败'}")
                    else:
                        st.info(status_message or "已检测到 RPA 状态文件")

                    if status_code:
                        st.caption(f"采购单查询码：{status_code}")
                    if status_time:
                        st.caption(f"状态更新时间：{status_time}")

                    return status_value
                else:
                    st.caption("暂未生成 RPA 请求。")
                    return ""

        status_box = st.empty()
        current_rpa_status = load_accessory_order_rpa_status()
        current_status_value = show_rpa_status(status_box, current_rpa_status)

        rpa_request_btn = st.button(
            "生成并启动 RPA 查询",
            use_container_width=True,
            key="btn_create_rpa_request_accessory"
        )

        if rpa_request_btn:
            blocked, blocked_message = _is_accessory_rpa_forbidden_now()
            rpa_internal_code = str(internal_code or "").strip()

            if blocked:
                st.error(f"⛔ {blocked_message}")
            elif not rpa_internal_code:
                st.error("请先填写采购单查询码")
            else:
                try:
                    current_status = load_accessory_order_rpa_status()
                    current_status_value = str(current_status.get("status", "")).strip()

                    if current_status_value in ("pending", "running"):
                        st.warning("当前已有 RPA 查询正在进行，请等待完成后再启动新的查询。")
                    else:
                        request_result = write_accessory_order_rpa_request(rpa_internal_code)

                        if not request_result.get("success"):
                            st.error(request_result.get("message", "生成 RPA 请求文件失败"))
                        else:
                            start_result = start_accessory_order_rpa()

                            st.success("已生成 RPA 请求文件")
                            st.info(f"采购单查询码：{request_result.get('internal_code', rpa_internal_code)}")
                            st.code(request_result.get("request_path", ""), language="text")

                            if start_result.get("success"):
                                st.success(start_result.get("message", "影刀 RPA 已启动，请等待状态更新"))

                                if start_result.get("already_running"):
                                    st.caption("检测到影刀已经打开，本次没有重复打开新窗口。")
                                elif start_result.get("pid"):
                                    st.caption(f"影刀进程 PID：{start_result.get('pid')}")
                            else:
                                st.error(start_result.get("message", "影刀 RPA 启动失败"))

                except Exception as e:
                    st.error(f"生成并启动 RPA 查询失败: {e}")

        should_poll_rpa = (
            st.session_state.get("acc_rpa_status_polling", False)
            or current_status_value in ("pending", "running")
        )

        if should_poll_rpa:
            import time

            st.caption("⏱️ 正在自动刷新 RPA 状态，每 5 秒更新一次。查询成功或失败后会停止。")

            # 最多轮询 5 分钟，避免异常情况下页面一直卡住
            for _ in range(60):
                latest_status = load_accessory_order_rpa_status()
                latest_value = show_rpa_status(status_box, latest_status)

                if latest_value in ("success", "failed"):
                    st.session_state["acc_rpa_status_polling"] = False
                    break

                if latest_value not in ("pending", "running"):
                    st.session_state["acc_rpa_status_polling"] = False
                    break

                time.sleep(5)

        st.divider()

        rpa_result_btn = st.button(
            "读取 RPA 结果并生成辅料下单表",
            use_container_width=True,
            key="btn_rpa_accessory"
        )

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
                                key="download_rpa_accessory",
                                on_click=_reset_accessory_form_state
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
