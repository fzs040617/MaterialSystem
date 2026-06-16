import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import datetime
import json
import time
import base64
import hashlib
import threading  # 👈 新增：引入多线程模块

# 导入自定义模块
from database import get_db_conn, load_data
from business_logic import load_user_draft, auto_save_draft, clear_user_draft
from excel_engines import generate_rw_purchase_contract_excel
# 👈 新增：引入你写好的采购合同飞书同步函数
from sync_history_to_feishu import migrate_purchase_orders_to_feishu

# 🌟 新增：引入飞书核销函数
from modules.feishu_portal import mark_as_migrated

def get_today_contract_no():
    """自动生成当天的合同流水号"""
    conn = get_db_conn(); c = conn.cursor()
    # 注意：MySQL 的日期格式匹配
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    date_part = datetime.date.today().strftime("%Y%m%d")
    try:
        c.execute("SELECT COUNT(*) FROM purchase_orders WHERE create_time LIKE %s", (f"{today_str}%",))
        count = c.fetchone()[0]
        return f"RW-{date_part}-{count + 1:03d}"
    except: return f"RW-{date_part}-001"
    finally: conn.close()

def render_purchase_order(uname):
    st.header("🛒 向工厂下单 (生成订购合同)")
    st.caption("系统将自动分配专属合同流水号，并生成带有润微公司抬头的标准原材料订购合同。")
    
    # 1. 乙方：选择包装袋发货源工厂
    df_pack = load_data("packaging_factories")
    pack_facts = df_pack['name'].tolist() if not df_pack.empty else []
    
    if not pack_facts:
        st.warning("⚠️ 基础数据不足：请先在【⚙️ 后台】录入包装袋工厂！")
        return
        
    contract_no = get_today_contract_no()
    st.info(f"📄 当前系统自动分配的合同编号：**{contract_no}**")
    
    c1, c2 = st.columns([1, 2])
    with c1:
        sel_fac = st.selectbox("🏭 乙方 (接单工厂)", ["-- 请选择 --"] + pack_facts)
        
    st.markdown("##### 📝 录入物料明细 (支持键盘复制粘贴、增删行)")
    
    # 2. 动态表格录入
    # === [修改点 1: 智能加载草稿箱] ===
    if 'rw_po_items' not in st.session_state:
        # 尝试从数据库去捞这个用户的草稿
        draft_df, draft_time = load_user_draft(uname, 'purchase_order')
        
        if draft_df is not None and not draft_df.empty:
            # === [之前加的补丁：处理货期] ===
            if '货期' in draft_df.columns:
                draft_df['货期'] = pd.to_datetime(draft_df['货期'], errors='coerce')
                draft_df['货期'] = draft_df['货期'].apply(lambda x: x.date() if pd.notnull(x) else None)
            
            # === [之前加的补丁：处理材质] ===
            if '材质' not in draft_df.columns:
                draft_df.insert(2, '材质', "")
                
            # 🌟 新增补丁：确保老草稿里面也有暗码列，防止报错
            if 'feishu_record_id' not in draft_df.columns:
                draft_df.insert(0, 'feishu_record_id', "")
                
            # ==========================================
            # 🌟 [本次新增核心补丁：强制锁定单价为浮点数]
            # 防止 Pandas 把之前全为0的单价列误认为整数列
            # ==========================================
            if '单价' in draft_df.columns:
                draft_df['单价'] = draft_df['单价'].astype(float)
            
            st.session_state['rw_po_items'] = draft_df
            st.session_state['po_draft_time'] = draft_time
        else:
            # 修改后的初始化模板（增加“feishu_record_id”隐藏列）
            # 分别在首次初始化，和清空草稿后的初始化处添加 "图片": ""
            st.session_state['rw_po_items'] = pd.DataFrame([{
                "feishu_record_id": "", 
                "图片": "",  # 👈 新增：图片空位
                "物料编号": "", "物料名称": "", "材质": "", "颜色": "", "尺寸": "", "收货标准": "", 
                "数量": 0, "单位": "Pcs", "单价": 0.0, "货期": None, "备注": "" 
            } for _ in range(3)])
            st.session_state['po_draft_time'] = None

    if st.session_state.get('po_draft_time'):
        # 按照 4:1 的比例切分两列，左边放提示，右边放按钮
        c_info, c_btn = st.columns([4, 1])
        
        with c_info:
            st.info(f"💡 系统已自动为您恢复上次未完成的草稿 (保存时间: {st.session_state['po_draft_time']})")
            
        with c_btn:
            if st.button("🗑️ 废弃当前草稿", use_container_width=True):
                # 1. 彻底清空数据库里的历史草稿
                clear_user_draft(uname, 'purchase_order')
                
                # 2. 把当前页面的表格强行恢复成崭新的 3 行空白模板 (含暗码列)
                        # 分别在首次初始化，和清空草稿后的初始化处添加 "图片": ""
                st.session_state['rw_po_items'] = pd.DataFrame([{
                    "feishu_record_id": "", 
                    "图片": "",  # 👈 新增：图片空位
                    "物料编号": "", "物料名称": "", "材质": "", "颜色": "", "尺寸": "", "收货标准": "", 
                    "数量": 0, "单位": "Pcs", "单价": 0.0, "货期": None, "备注": "" 
                } for _ in range(3)])
                
                # 3. 清除提示标记，并让页面瞬间刷新
                st.session_state['po_draft_time'] = None
                st.rerun()
        
    data_hash = hashlib.md5(st.session_state['rw_po_items'].to_json().encode()).hexdigest()
    edited_df = st.data_editor(
        st.session_state['rw_po_items'],
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "feishu_record_id": None, # 👈 核心：在前端彻底隐藏这个系统暗码列
            "图片": st.column_config.ImageColumn("🖼️ 图片 (可粘贴链接)"),
            "物料编号": st.column_config.TextColumn("物料编号"),
            "物料名称": st.column_config.TextColumn("物料名称"),
            "材质": st.column_config.TextColumn("材质"),
            "颜色": st.column_config.TextColumn("颜色"),
            "尺寸": st.column_config.TextColumn("尺寸"),
            "收货标准": st.column_config.TextColumn("收货标准"),
            "数量": st.column_config.NumberColumn("数量", min_value=0, step=1000, format="%d"),
            "单位": st.column_config.TextColumn("单位"),
            "单价": st.column_config.NumberColumn("单价(含税运)", min_value=0.0, step=0.00001, format="%.4f"),
            "货期": st.column_config.DateColumn("货期", format="YYYY-MM-DD"), # <--- 改成 DateColumn 并指定格式
            "备注": st.column_config.TextColumn("备注")
        }
        
    )
    # 采购合同的模块名应该是 purchase_order
    auto_save_draft(uname, 'purchase_order', edited_df)
    st.write("")
    
    # 1. 提前提取并过滤当前表格中的有效数据
    items = edited_df.to_dict('records')
    valid_items = [i for i in items if (str(i.get('物料名称','')).strip() or str(i.get('物料编号','')).strip())]

    # 2. 定义一个入库的“暗箱操作”函数 (当用户点击下载按钮时自动触发)
    def save_po_to_db(blob_data):
        conn = get_db_conn()
        c = conn.cursor()
        try:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            def date_converter(obj):
                if isinstance(obj, (datetime.date, datetime.datetime)):
                    return obj.isoformat()
                return obj
            
            items_json = json.dumps(valid_items, default=date_converter, ensure_ascii=False)
            
            # 注意：增加了 status 列，默认值为 'pending'
            c.execute('''INSERT INTO purchase_orders 
                         (create_time, factory_name, is_tax_inclusive, remark, excel_data, operator, contract_no, status)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''', 
                      (now_str, sel_fac, 1, items_json, blob_data, uname, contract_no, 'pending'))
            conn.commit()

            # ==========================================
            # 🌟 新增：核算本单到底消耗了哪些飞书申请
            # 只有用户真正点击下载确认单据生成了，才去写黑名单
            # ==========================================
            consumed_feishu_ids = []
            for v_item in valid_items:
                f_id = str(v_item.get("feishu_record_id", "")).strip()
                # 防止空值和 pandas 转换时可能产生的 nan
                if f_id and f_id != "nan" and f_id != "None":
                    consumed_feishu_ids.append(f_id)
            
            if consumed_feishu_ids:
                # 1. 记录落盘到 MySQL 黑名单
                mark_as_migrated(consumed_feishu_ids) 
                
                # 2. 🌟 新增：直接对当前内存中的飞书同步记录进行实时剔除，实现无感刷新
                if 'feishu_df' in st.session_state and not st.session_state.feishu_df.empty:
                    if 'record_id' in st.session_state.feishu_df.columns:
                        st.session_state.feishu_df = st.session_state.feishu_df[
                            ~st.session_state.feishu_df['record_id'].isin(consumed_feishu_ids)
                        ]
            # ==========================================

            # 🌟 数据落盘后，立刻开启后台线程同步回飞书多维表格
            try:
                sync_thread = threading.Thread(target=migrate_purchase_orders_to_feishu)
                sync_thread.start()
            except Exception as e:
                print(f"后台触发飞书同步失败: {e}") # 就算同步失败，也不影响用户正常下载合同
            
            # 清空表格 (包含暗码字段)
            st.session_state['rw_po_items'] = pd.DataFrame([{
                "feishu_record_id": "", # 👈 清空时保留骨架
                "物料编号": "", "物料名称": "", "材质": "", "颜色": "", "尺寸": "", "收货标准": "", 
                "数量": 0, "单位": "Pcs", "单价": 0.0, "货期": None, "备注": "" 
            } for _ in range(3)])
            
            # 清除草稿
            clear_user_draft(uname, 'purchase_order')
            
            st.toast("✅ 订单提交成功，合同已自动下载并保存至历史记录！", icon="🎉")
        except Exception as e:
            st.error(f"保存记录失败: {e}")
        finally:
            conn.close()

    # 3. 智能按钮渲染逻辑
    # 如果必填项没填完，我们显示一个长得一模一样的假按钮来报错拦截
    if sel_fac == "-- 请选择 --" or not valid_items:
        if st.button("🚀 一键提交订单并下载合同", type="primary"):
            if sel_fac == "-- 请选择 --":
                st.error("❌ 必填项缺失：请选择乙方工厂！")
            else:
                st.error("❌ 必填项缺失：请至少填写一行物料信息！")
    else:
        # 如果填完了，我们在后台内存里瞬间把 Excel 准备好
        excel_blob = generate_rw_purchase_contract_excel(contract_no, sel_fac, valid_items)
        dl_name = f"原材料订购合同_{sel_fac}_{contract_no}.xlsx"
        
        # 显示真正的“下载按钮”，并绑定我们的 save_po_to_db 写入函数
        st.download_button(
            label="🚀 一键提交订单并下载合同",
            data=excel_blob,
            file_name=dl_name,
            type="primary",
            on_click=save_po_to_db,
            args=(excel_blob,)
        )