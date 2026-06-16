import streamlit as st
import pandas as pd
import datetime
import time
from feishu_sync import fetch_feishu_data_as_df
from database import get_db_conn  # 👈 引入数据库连接

# ==========================================
# 🌟 新增：已迁移记录的数据库黑名单管理
# ==========================================
def init_migrated_table():
    """初始化已迁移记录表，防止表不存在报错"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS migrated_feishu_records (
            feishu_record_id VARCHAR(100) PRIMARY KEY,
            migrated_at DATETIME
        )
    ''')
    conn.commit()
    conn.close()

def get_migrated_ids():
    """获取所有已经迁移过的飞书 record_id"""
    conn = get_db_conn()
    c = conn.cursor()
    try:
        c.execute("SELECT feishu_record_id FROM migrated_feishu_records")
        rows = c.fetchall()
        return set([r[0] for r in rows])
    except:
        return set()
    finally:
        conn.close()

def mark_as_migrated(record_ids):
    """将选中的 record_id 存入数据库黑名单"""
    if not record_ids: return
    conn = get_db_conn()
    c = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for rid in record_ids:
        # 使用 INSERT IGNORE 防止重复点击导致的报错
        c.execute("INSERT IGNORE INTO migrated_feishu_records (feishu_record_id, migrated_at) VALUES (%s, %s)", (rid, now_str))
    conn.commit()
    conn.close()


def show_feishu_sync(uname):
    st.title("🔗 飞书采购申请同步")
    st.markdown("---")
    
    # 确保数据库表存在
    init_migrated_table()
    
    col1, col2 = st.columns([1, 4])
    with col1:
        sync_btn = st.button("🔄 同步飞书数据", type="primary", use_container_width=True)
    with col2:
        st.info("系统会自动隐藏已迁移过的记录。勾选下方的申请记录，点击‘迁移’即可生成下单明细。")

    if "feishu_df" not in st.session_state:
        st.session_state.feishu_df = pd.DataFrame()
        
    if "rw_po_items" not in st.session_state or st.session_state.rw_po_items is None:
        all_columns = ["feishu_record_id", "图片", "物料编号", "物料名称", "材质", "颜色", "尺寸", "收货标准", "数量", "单位", "单价", "货期", "备注"]
        st.session_state.rw_po_items = pd.DataFrame(columns=all_columns)
    else:
        all_columns = ["feishu_record_id", "图片", "物料编号", "物料名称", "材质", "颜色", "尺寸", "收货标准", "数量", "单位", "单价", "货期", "备注"]
        for col in all_columns:
            if col not in st.session_state.rw_po_items.columns:
                st.session_state.rw_po_items[col] = 0 if col == "数量" else ""

    # ==========================================
    # 🌟 修改点 1：拉取数据时，进行黑名单过滤
    # ==========================================
    if sync_btn:
        with st.spinner("正在拉取并过滤已处理数据..."):
            raw_df = fetch_feishu_data_as_df()
            
            if not raw_df.empty:
                if 'record_id' not in raw_df.columns:
                    st.error("⚠️ 致命错误：飞书数据中缺失 `record_id` 列！请检查 feishu_sync.py 的解析逻辑。")
                    st.session_state.feishu_df = raw_df
                else:
                    migrated_ids = get_migrated_ids()
                    # 过滤掉那些在 migrated_ids 里的行
                    filtered_df = raw_df[~raw_df['record_id'].isin(migrated_ids)]
                    st.session_state.feishu_df = filtered_df
            else:
                st.session_state.feishu_df = pd.DataFrame()

    if not st.session_state.feishu_df.empty:
        df_display = st.session_state.feishu_df.copy()
        df_display.insert(0, "选择", False)
        
        # 为了不让用户看到一堆乱七八糟的 record_id，我们可以在前端隐藏它
        hide_cols = True if 'record_id' in df_display.columns else False
        display_cols = [c for c in df_display.columns if c != 'record_id'] if hide_cols else df_display.columns.tolist()
        
        edited_df = st.data_editor(
            df_display,
            column_config={
                "选择": st.column_config.CheckboxColumn("迁移", default=False),
                "图片 （要用最新的图）": st.column_config.ImageColumn("预览图"),
                "record_id": None  # 👈 在前端彻底隐藏这个系统暗码列
            },
            hide_index=True,
            use_container_width=True,
            disabled=df_display.columns.difference(["选择"]).tolist(),
            key="feishu_selector"
        )
        
        selected_rows = edited_df[edited_df["选择"] == True]
        if not selected_rows.empty:
            st.info(f"已选择 {len(selected_rows)} 条记录")
            if st.button("🚀 迁移选中记录至采购下单", type="primary"):
                imported_items = []
                migrated_record_ids = [] # 👈 收集本次迁移的 record_id
                
                for _, row in selected_rows.iterrows():
                    # 记录被选中的飞书系统ID
                    if 'record_id' in row and row['record_id']:
                        migrated_record_ids.append(row['record_id'])
                        
                    # ==========================================
                    # 🌟 核心修改点：分别精准提取“名称”和“尺寸”
                    # ==========================================
                    material_code = str(row.get("物料编号（条码）", "")).strip()
                    if material_code in ["nan", "None"]: 
                        material_code = ""

                    material_name = str(row.get("申购物料名称", "")).strip()
                    if material_name in ["nan", "None"]: 
                        material_name = ""
                        
                    size_val = str(row.get("尺寸/cm", "")).strip()
                    if size_val in ["nan", "None"]: 
                        size_val = ""
                    
                    # 提取数量（增加浮点数容错，防止带小数点的数量报错）
                    qty = row.get("申购数量", 0)
                    try:
                        qty = int(float(qty))
                    except:
                        qty = 0

                    # 提取货期
                    delivery = row.get("货期", None)
                    if delivery and not isinstance(delivery, str):
                        try:
                            delivery = delivery.strftime('%Y-%m-%d')
                        except:
                            delivery = None
                    else:
                        delivery = delivery if delivery else None
                    
                    # 提取备注
                    remark = ""
                    
                        # 提取飞书带过来的图片数据（feishu_sync 里已经转成了 base64）
                    img_val = row.get("图片 （要用最新的图）", "")

                    item = {
                        "feishu_record_id": row.get('record_id', ''),
                        "图片": img_val,  # 👈 新增：图片列映射
                        "物料编号": material_code,
                        "物料名称": material_name,
                        "材质": "",
                        "颜色": "",
                        "尺寸": size_val,
                        "收货标准": "",
                        "数量": qty,
                        "单位": "Pcs",
                        "单价": 0.0,
                        "货期": delivery, 
                        "备注": remark
                    }
                    imported_items.append(item)
                
              
                # ==========================================
                # 🌟 修改点 2：将选中的记录 ID 写入本地数据库
                # 勾选迁移后，立刻加入黑名单，避免下次重复出现
                # ==========================================
                if migrated_record_ids:
                    mark_as_migrated(migrated_record_ids)

                    # 同步将这些行从当前页面数据中剔除，实现刷新后不再显示
                    if 'feishu_df' in st.session_state and not st.session_state.feishu_df.empty:
                        if 'record_id' in st.session_state.feishu_df.columns:
                            st.session_state.feishu_df = st.session_state.feishu_df[
                                ~st.session_state.feishu_df['record_id'].isin(migrated_record_ids)
                            ]

                df_imported = pd.DataFrame(imported_items)
                df_imported['货期'] = pd.to_datetime(df_imported['货期'], errors='coerce')
                df_imported['货期'] = df_imported['货期'].apply(lambda x: x.date() if pd.notnull(x) else None)
                
                st.session_state.rw_po_items = df_imported
                st.session_state.po_draft_time = None 

                from business_logic import clear_user_draft
                clear_user_draft(uname, 'purchase_order')

                st.success(f"已成功将 {len(imported_items)} 条物料添加到采购下单草稿！飞书申请记录已隐藏。")
                st.balloons()
                st.info("数据已就绪，正在为您刷新界面...")
                
                time.sleep(1.5)
                st.rerun()
    else:
        st.write("暂无待处理数据，请尝试点击同步最新数据。")