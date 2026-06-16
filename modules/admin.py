import streamlit as st
import pandas as pd
import datetime
import time
import os
import json
import io
from database import load_data, save_data, get_db_conn, add_column_to_db, drop_column_from_db
from business_logic import sync_master_data_to_inventory, rename_packaging_factory, register_user
from excel_engines import generate_inventory_excel_with_images, generate_monthly_report_excel
from config import INVITATION_CODE, IMAGE_FOLDER
# 🌟 [新增导入] 引入已经写好的飞书双向同步函数
from sync_feishu_to_mysql import sync_feishu_to_mysql
# ✅ 只保留这一行真正有效的核心库存推送函数导入：
from sync_mysql_to_feishu import sync_inventory_mysql_to_feishu, run_full_sync_flow, safe_log

def render_admin():
    st.header("⚙️ 后台 (管理员控制中心)")
    
    # ==========================================
    # 🌟 顶层现代化排版：5大核心业务模块
    # ==========================================
    tab_sync, tab_master, tab_inventory, tab_crossborder, tab_system = st.tabs([
        "🔄 同步控制中心",
        "📚 基础主数据(飞书同步)", 
        "📦 本地库存盘点", 
        "🌍 跨境物料管理", 
        "⚙️ 系统配置与权限"
    ])

    # ==========================================
    # 模块 1：🔄 同步控制中心 (集中管理所有网络拉取与推送)
    # ==========================================
    with tab_sync:
        st.subheader("🚀 核心数据全链路闭环")
        st.info("💡 流程引擎：抓取飞书档案 ➔ 数据库更新 ➔ 库存自动对齐 ➔ 规格权限全量镜像同步。")
        
        col1, col2 = st.columns([1, 2])
        with col1:
            if st.button("⚡ 一键全量同步 (拉取 -> 计算 -> 推送)", type="primary", use_container_width=True):
                with st.spinner("正在执行全流程底层数据对齐，请稍候..."):
                    try:
                        run_full_sync_flow()
                        st.success("🎉 全闭环同步圆满完成！数据已 1:1 镜像对齐。")
                    except Exception as e:
                        st.error(f"💥 同步过程中出现错误: {str(e)}")
                        safe_log(f"同步异常: {str(e)}")
        
        st.divider()
        st.subheader("🧩 独立模块拉取 (辅助同步)")
        c_sync1, c_sync2 = st.columns(2)
        with c_sync1:
            if st.button("📥 独立同步：制衣厂 (收货)", use_container_width=True):
                with st.spinner("正在从飞书拉取制衣厂数据..."):
                    try:
                        from sync_feishu_to_mysql import sync_feishu_garment_to_mysql
                        sync_feishu_garment_to_mysql()
                        st.success("🎉 已同步最新制衣厂数据！")
                        time.sleep(1); st.rerun()
                    except Exception as e:
                        st.error(f"同步失败: {e}")
        with c_sync2:
            if st.button("📥 独立同步：采购物料", use_container_width=True):
                with st.spinner("正在从飞书拉取物料主数据..."):
                    try:
                        from sync_feishu_to_mysql import sync_feishu_material_to_mysql
                        sync_feishu_material_to_mysql()
                        st.success("🎉 已同步最新物料数据！")
                        time.sleep(1); st.rerun()
                    except Exception as e:
                        st.error(f"同步失败: {e}")

    # ==========================================
    # 模块 2：📦 本地库存盘点 (数据覆写与导出)
    # ==========================================
    with tab_inventory:
        st.subheader("📊 实时库存矩阵调校")
        
        if 'temp_inv_df' not in st.session_state:
            st.session_state['temp_inv_df'] = load_data("inventory")
        
        ed_i = st.data_editor(
            st.session_state['temp_inv_df'], 
            num_rows="dynamic", 
            key="inventory_editor_widget", 
            use_container_width=True
        )
        
        if st.button("💾 确认保存至本地数据库", type="primary"): 
            success = save_data("inventory", ed_i)
            if success:
                st.session_state['temp_inv_df'] = ed_i
                st.success("✅ 库存数据已成功写入！若需上云请前往【同步控制中心】点击一键同步。")
                time.sleep(1); st.rerun()
            else:
                st.error("❌ 保存失败，请检查数据库连接。")

        st.divider()
        st.subheader("📥 高级报表导出")
        st.caption("导出包含物料主图的 Excel 清单，方便实物盘点与对账。")
        img_excel_data = generate_inventory_excel_with_images()
        st.download_button(
            label="🚀 一键导出带图库存明细 (Excel)",
            data=img_excel_data,
            file_name=f"包装袋实物盘点表_{datetime.date.today()}.xlsx",
            type="primary"
        )

    # ==========================================
    # 模块 3：🌍 跨境物料管理 (独立生态)
    # ==========================================
    with tab_crossborder:
        st.subheader("🌍 跨境物料独立管理中枢")
        cb_image_folder = os.path.join(IMAGE_FOLDER, "crossborder")
        os.makedirs(cb_image_folder, exist_ok=True)
        
        tab_add, tab_edit = st.tabs(["➕ 录入新物料", "🛠️ 现有物料管理"])
        
        with tab_add:
            st.caption("录入新物料。**物料编码必须唯一**")
            with st.form("add_crossborder_form"):
                col1, col2 = st.columns(2)
                with col1:
                    new_code = st.text_input("物料编码 (必填, 唯一)")
                    new_name_model = st.text_input("名称+型号 (必填)")
                with col2:
                    new_stock = st.number_input("初始库存", min_value=0, step=1, value=0)
                    new_image = st.file_uploader("上传图片 (可选)", type=["png", "jpg", "jpeg"], key="cb_add_img")
                submitted = st.form_submit_button("💾 保存新物料", type="primary")
                if submitted:
                    if not new_code or not new_name_model:
                        st.error("物料编码和名称型号不能为空！")
                    else:
                        conn = get_db_conn()
                        cur = conn.cursor()
                        try:
                            cur.execute("SELECT id FROM crossborder_materials_v2 WHERE material_code=%s", (new_code,))
                            if cur.fetchone():
                                st.error("❌ 物料编码已存在")
                            else:
                                img_path = ""
                                if new_image:
                                    from utils import clean_filename
                                    ext = new_image.name.split('.')[-1]
                                    safe_name = clean_filename(f"{new_code}_{new_name_model}")
                                    img_path = os.path.join(cb_image_folder, f"{safe_name}.{ext}")
                                    with open(img_path, "wb") as f:
                                        f.write(new_image.getbuffer())
                                cur.execute("""
                                    INSERT INTO crossborder_materials_v2 (material_code, name_model, image_path, stock_quantity)
                                    VALUES (%s, %s, %s, %s)
                                """, (new_code, new_name_model, img_path, new_stock))
                                conn.commit()
                                st.success("✅ 物料添加成功")
                                time.sleep(1); st.rerun()
                        except Exception as e:
                            conn.rollback()
                            st.error(f"保存失败: {e}")
                        finally:
                            conn.close()
        
        with tab_edit:
            st.caption("修改已有物料的信息、库存或更换图片。")
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT id, material_code, name_model, image_path, stock_quantity FROM crossborder_materials_v2 ORDER BY id")
            materials = cur.fetchall()
            conn.close()
            
            if not materials:
                st.info("暂无物料数据，请先在「新增」标签页添加。")
            else:
                material_opts = [f"{m[1]} - {m[2]} (ID:{m[0]})" for m in materials]
                selected_label = st.selectbox("选择要编辑的物料", material_opts, key="cb_edit_select")
                idx = material_opts.index(selected_label)
                selected = materials[idx]
                mat_id, code, name_model, img_path, stock = selected
                
                with st.form("edit_crossborder_form"):
                    col1, col2 = st.columns(2)
                    with col1:
                        new_code = st.text_input("物料编码", value=code)
                        new_name_model = st.text_input("名称+型号", value=name_model)
                    with col2:
                        new_stock = st.number_input("库存数量", value=stock, step=1)
                        st.caption("当前图片：")
                        if img_path and os.path.exists(img_path):
                            st.image(img_path, width=150)
                        else:
                            st.caption("无图片")
                        new_image = st.file_uploader("更换图片 (可选)", type=["png", "jpg", "jpeg"], key="cb_edit_img")
                    
                    col_btn1, col_btn2 = st.columns(2)
                    submitted = col_btn1.form_submit_button("💾 确认修改", type="primary")
                    delete_clicked = col_btn2.form_submit_button("🗑️ 删除此物料", type="secondary")
                    
                    if submitted:
                        if not new_code or not new_name_model:
                            st.error("物料编码和名称型号不能为空")
                        else:
                            conn = get_db_conn()
                            cur2 = conn.cursor()
                            try:
                                cur2.execute("SELECT id FROM crossborder_materials_v2 WHERE material_code=%s AND id!=%s", (new_code, mat_id))
                                if cur2.fetchone():
                                    st.error("物料编码已被其他物料使用")
                                else:
                                    new_img_path = img_path
                                    if new_image:
                                        if img_path and os.path.exists(img_path):
                                            os.remove(img_path)
                                        from utils import clean_filename
                                        ext = new_image.name.split('.')[-1]
                                        safe_name = clean_filename(f"{new_code}_{new_name_model}")
                                        new_img_path = os.path.join(cb_image_folder, f"{safe_name}.{ext}")
                                        with open(new_img_path, "wb") as f:
                                            f.write(new_image.getbuffer())
                                    cur2.execute("""
                                        UPDATE crossborder_materials_v2
                                        SET material_code=%s, name_model=%s, image_path=%s, stock_quantity=%s
                                        WHERE id=%s
                                    """, (new_code, new_name_model, new_img_path, new_stock, mat_id))
                                    conn.commit()
                                    st.success("物料信息已更新")
                                    time.sleep(1); st.rerun()
                            except Exception as e:
                                conn.rollback()
                                st.error(f"修改失败: {e}")
                            finally:
                                conn.close()
                    
                    if delete_clicked:
                        st.warning(f"确认删除物料「{code} - {name_model}」？")
                        confirm = st.checkbox("我确认要永久删除该物料及其图片")
                        if confirm:
                            conn = get_db_conn()
                            cur2 = conn.cursor()
                            try:
                                if img_path and os.path.exists(img_path):
                                    os.remove(img_path)
                                cur2.execute("DELETE FROM crossborder_materials_v2 WHERE id=%s", (mat_id,))
                                conn.commit()
                                st.success("物料已删除")
                                time.sleep(1); st.rerun()
                            except Exception as e:
                                conn.rollback()
                                st.error(f"删除失败: {e}")
                            finally:
                                conn.close()

    # ==========================================
    # 模块 4：📚 基础主数据 (镜像预览区)
    # ==========================================
    with tab_master:
        st.info("📌 此页数据全面托管于飞书多维表格。本地仅提供只读镜像预览，若需最新数据请在飞书编辑后前往【同步控制中心】拉取。")
        
        sub_t1, sub_t2, sub_t3, sub_t4 = st.tabs(["🛍️ 包装袋规格", "🏭 包装袋工厂信息", "🏭 制衣厂地址联系方式", "📦 采购主物料信息"])
        
        with sub_t1:
            specs_df = load_data("bag_specs")
            if not specs_df.empty:
                if 'sort_order' not in specs_df.columns:
                    specs_df['sort_order'] = 0
                specs_df['sort_order'] = specs_df['sort_order'].fillna(0).astype(int)
                specs_df['sort_key'] = specs_df['sort_order'].apply(lambda x: x if x > 0 else 999999)
                specs_df = specs_df.sort_values(['sort_key', 'name']).drop(columns=['sort_key'])
                
                # 🌟 修改：移除已经废弃的 unit_price 单价列
                st.dataframe(
                    specs_df[['name', 'size', 'belong_to', 'sort_order']],
                    column_config={
                        "name": "名称", "size": "尺寸", 
                        "belong_to": "归属工厂", "sort_order": "排序"
                    },
                    hide_index=True, use_container_width=True
                )
            else:
                st.warning("暂无包装袋规格数据。")
                
        with sub_t2:
            df_pf = load_data("packaging_factories")
            if not df_pf.empty:
                if 'factory_type' not in df_pf.columns: df_pf['factory_type'] = '包装袋'
                st.dataframe(
                    df_pf[['name', 'contact', 'factory_type', 'address', 'manager']], 
                    column_config={
                        "name": "工厂名称 (乙方)", 
                        "contact": "联系方式", 
                        "factory_type": "业务类型",
                        "address": "地址",           # 👈 新增：映射展示名
                        "manager": "负责人"         # 👈 新增：映射展示名
                    },
                    hide_index=True, use_container_width=True
                )
            else:
                st.warning("暂无发货工厂档案。")
            
            with st.expander("✏️ 工厂强制重命名 (高级)"):
                st.caption("除非极特殊情况，建议在飞书直接修改名称。")
                f_list = df_pf['name'].tolist() if not df_pf.empty else []
                c_r1, c_r2, c_r3 = st.columns(3)
                with c_r1: old_f = st.selectbox("原名称", f_list) if f_list else st.selectbox("无", ["无"])
                with c_r2: new_f = st.text_input("新名称")
                with c_r3:
                    st.write(""); st.write("")
                    if st.button("强制执行重命名"):
                        if new_f and f_list:
                            ok, msg = rename_packaging_factory(old_f, new_f)
                            if ok: st.success(msg); time.sleep(1); st.rerun()
                            else: st.error(msg)
                        else: st.error("请验证输入项。")
                        
        with sub_t3:
            df_gf = load_data("garment_factories")
            if not df_gf.empty:
                st.dataframe(df_gf, hide_index=True, use_container_width=True)
            else:
                st.warning("暂无制衣厂数据。")
                
        with sub_t4:
            df_material = load_data("material_master")
            if not df_material.empty:
                def parse_json_array(val):
                    if pd.isna(val) or val == "" or val == "[]": return ""
                    try:
                        if isinstance(val, str):
                            arr = json.loads(val)
                            return ", ".join(str(v) for v in arr) if isinstance(arr, list) else str(arr)
                        return str(val)
                    except: return str(val)
                
                df_material['单价显示'] = df_material['unit_price'].apply(parse_json_array)
                df_material['税率显示'] = df_material['tax_rate'].apply(parse_json_array)
                
                display_cols = ['material_code', 'product_name', 'specification', 'color', 'unit', '单价显示', '税率显示']
                existing_cols = [col for col in display_cols if col in df_material.columns]
                st.dataframe(
                    df_material[existing_cols],
                    column_config={
                        "material_code": "物料编码", "product_name": "货品名称", "specification": "规格",
                        "color": "颜色", "unit": "单位", "单价显示": "单价", "税率显示": "税率"
                    },
                    hide_index=True, use_container_width=True
                )
            else:
                st.warning("暂无物料数据。")

    # ==========================================
    # 模块 5：⚙️ 系统配置与权限 (基础架构设置)
    # ==========================================
    with tab_system:
        with st.expander("🔗 国际69码数据库映射", expanded=False):
            st.caption("管理【内部条码】与【国际69码】的对应关系。下单生成时将据此自动写入第一列。")
            
            c_up1, c_up2 = st.columns([1, 1])
            with c_up1:
                uploaded_mapping = st.file_uploader("📥 Excel 批量导入 (需包含 '条码' 和 '69码' 两列)", type=['xlsx', 'xls'])
                if uploaded_mapping and st.button("🚀 执行合并导入", type="primary"):
                    try:
                        df_upload = pd.read_excel(uploaded_mapping, dtype=str)
                        for col in df_upload.columns:
                            df_upload[col] = df_upload[col].astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
                            
                        barcode_col = code69_col = None
                        for col in df_upload.columns:
                            valid_data = df_upload[col][df_upload[col] != 'nan']
                            if valid_data.empty: continue
                            if valid_data.str.match(r'^69\d{11}$').sum() > 0: code69_col = col
                            elif valid_data.str.match(r'^R[A-Za-z]\d{12}$').sum() > 0: barcode_col = col

                        if barcode_col and code69_col:
                            st.info(f"💡 智能识别成功！识别到内部条码列：【{barcode_col}】，69码列：【{code69_col}】")
                            new_df = df_upload[[barcode_col, code69_col]].rename(columns={barcode_col: 'barcode', code69_col: 'code_69'})
                            old_df = load_data("barcode_mapping")
                            combined_df = pd.concat([old_df, new_df]).drop_duplicates(subset=['barcode'], keep='last')
                            save_data("barcode_mapping", combined_df)
                            st.success(f"✅ 成功导入/更新 {len(new_df)} 条数据！")
                            time.sleep(2); st.rerun()
                        else:
                            st.error("❌ 智能识别失败：未能在表中找到符合【14位R开头内部条码】和【13位69码】特征的数据列。")
                    except Exception as e:
                        st.error(f"导入失败: {e}")
                        
            with c_up2:
                st.info("💡 也可以直接在下方表格里双击单元格进行手动新增或修改。")
                
            df_barcode = load_data("barcode_mapping")
            if df_barcode.empty: df_barcode = pd.DataFrame(columns=['barcode', 'code_69'])
                
            edited_barcode = st.data_editor(
                df_barcode, num_rows="dynamic", use_container_width=True, key="barcode_editor",
                column_config={"barcode": st.column_config.TextColumn("内部条码 (原表条码)", required=True), "code_69": st.column_config.TextColumn("对应的国际69码", required=True)}
            )
            if st.button("💾 保存手动修改的映射库"):
                save_data("barcode_mapping", edited_barcode)
                st.success("✅ 保存成功！"); time.sleep(1); st.rerun()
                
        with st.expander("📊 月度数据报表 (Monthly Report)"):
            c1, c2, c3 = st.columns(3); now = datetime.date.today()
            with c1: y = st.number_input("年", 2020, 2030, now.year)
            with c2: m = st.selectbox("月", range(1,13), now.month-1)
            with c3: 
                st.write(""); st.write("")
                if st.button("生成月报"):
                    rd = generate_monthly_report_excel(y, m)
                    if rd: st.download_button(f"📥 下载 {y}年{m}月报表", rd, f"Report_{y}_{m}.xlsx", "application/vnd...", type="primary")
                    else: st.warning("无数据")

        with st.expander("👤 账号鉴权管理"):
            c1, c2 = st.columns(2)
            with c1:
                nu = st.text_input("新账号"); np = st.text_input("密码", type="password"); nr = st.selectbox("角色", ["sales", "warehouse", "admin"])
                if st.button("➕ 创建授权"):
                    if register_user(nu, np, nr): st.success("成功")
                    else: st.error("失败")
            with c2: 
                st.dataframe(load_data("users")[['username','role']], hide_index=True)

        with st.expander("💾 数据高可用备份"):
            st.info("💡 提示：底层已升级为 MySQL 关系型数据库集群，请联系 DBA (数据库管理员) 通过 Navicat 等专业工具配置自动快照。")

# --- 尾部保留的基础方法，勿删 ---
def add_column_to_db(table_name, column_name, column_type="TEXT"):
    conn = get_db_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
        conn.commit()
        return True
    except Exception as e:
        print(f"添加列失败: {e}")
        return False
    finally:
        conn.close()

def drop_column_from_db(table_name, column_name):
    conn = get_db_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(f"ALTER TABLE {table_name} DROP COLUMN {column_name}")
        conn.commit()
        return True
    except Exception as e:
        print(f"删除列失败: {e}")
        return False
    finally:
        conn.close()