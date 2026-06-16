import streamlit as st
import pandas as pd
import datetime
import time

# 导入自定义模块
from database import get_db_conn, load_data
from business_logic import add_inventory
from utils import format_number_cn

# ==========================================
# 1. 包装袋入库补货页面
# ==========================================

def render_inbound(uname):
    """渲染入库补货界面"""
    st.header("📥 入库补货")
    c1, c2 = st.columns(2)
    
    with c1:
        # 获取包装袋类型的源工厂列表
        df_pack = load_data("packaging_factories")
        if not df_pack.empty and 'factory_type' in df_pack.columns:
            rf = df_pack[df_pack['factory_type'].isin(['包装袋', None, ''])]['name'].tolist()
        else:
            rf = df_pack['name'].tolist() if not df_pack.empty else []
        
        f_opts = sorted(list(set([x for x in rf if x and str(x).strip()!=''])))
        f = st.selectbox("选择入库工厂", f_opts) if f_opts else st.selectbox("工厂", ["(请先去后台添加工厂)"])
        
        # 获取并排序包装袋规格
        ds = load_data("bag_specs")
        if ds.empty: 
            st.warning("⚠️ 请先在后台配置包装袋规格")
            return
        
        if 'sort_order' not in ds.columns: ds['sort_order'] = 0
        ds['sort_order'] = ds['sort_order'].fillna(0).astype(int)
        ds['sort_key'] = ds['sort_order'].apply(lambda x: x if x > 0 else 999999)
        ds = ds.sort_values(['sort_key', 'name'])
        
        ds['opt'] = ds['name'] + " | " + ds['size']
        opts = [x for x in ds['opt'].tolist() if x and str(x).strip()!='']
        sel_opt = st.selectbox("选择包装袋 (名称 | 尺寸)", opts)
        b_name, b_size = sel_opt.split(" | ")

    with c2:
        q = st.number_input("入库数量", min_value=0, step=1000)
        if q > 0: 
            st.caption(f"👀 确认数量：{format_number_cn(q)}")
        n = st.text_input("入库备注 (选填)", placeholder="例如：4月第一批补货")

    st.divider()
    if st.button("🚀 确认执行入库", type="primary", use_container_width=True):
        if q > 0:
            # 调用业务逻辑层执行入库事务
            ok, msg = add_inventory(f, b_name, b_size, q, n, uname)
            if ok: 
                st.success("✅ 入库成功！库存已更新。")
                time.sleep(1)
                st.rerun()
            else: 
                st.error(f"❌ 入库失败：{msg}")
        else: 
            st.warning("⚠️ 入库数量必须大于 0")

    # 展示最近的入库流水
    st.subheader("🕒 最近 10 条入库记录")
    idb = load_data("inbound_history")
    if not idb.empty: 
        st.dataframe(
            idb.sort_values("id", ascending=False).head(10), 
            use_container_width=True, 
            hide_index=True
        )

# ==========================================
# 2. 制衣厂消耗登记页面
# ==========================================

def render_garment_consumption(uname):
    """渲染制衣厂包装袋消耗登记界面"""
    st.markdown("### 👕 制衣厂消耗登记")
    st.info("💡 提示：此模块用于记录制衣厂在生产过程中实际消耗的袋子数量，便于月末对账。")
    
    # 获取基础资料
    g_facts = load_data("garment_factories")['name'].tolist() if not load_data("garment_factories").empty else []
    specs_df = load_data("bag_specs")
    specs_list = (specs_df['name'] + " | " + specs_df['size']).tolist() if not specs_df.empty else []
        
    if not g_facts or not specs_list:
        st.warning("⚠️ 基础数据不足：请先确保已录入【制衣厂】和【包装袋规格】")
        return
        
    with st.form("consume_form", clear_on_submit=True):
        st.write("📝 **填写消耗详情**")
        c1, c2 = st.columns(2)
        
        with c1:
            sel_fac = st.selectbox("🏭 消耗方 (制衣厂)", ["-- 请选择 --"] + g_facts)
            order_no = st.text_input("🧾 关联订单号 / 款号", placeholder="必填项")
            
        with c2:
            sel_spec = st.selectbox("📦 消耗包装袋", ["-- 请选择 --"] + specs_list)
            qty = st.number_input("🔢 消耗数量", min_value=1, value=100, step=100)
            
        submit = st.form_submit_button("💾 提交登记", type="primary", use_container_width=True)
        
        if submit:
            if sel_fac == "-- 请选择 --" or sel_spec == "-- 请选择 --" or not str(order_no).strip():
                st.error("❌ 请完整填写工厂、包装袋和订单号！")
            else:
                bag_n, bag_s = sel_spec.split(" | ", 1)
                now_str = datetime.date.today().strftime("%Y-%m-%d")
                
                # 直接通过原生连接写入消耗记录
                conn = get_db_conn(); c = conn.cursor()
                try:
                    c.execute('''
                        INSERT INTO garment_consumption (consume_date, factory_name, order_no, bag_name, bag_size, quantity, operator)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ''', (now_str, sel_fac, order_no.strip(), bag_n, bag_s, qty, uname))
                    conn.commit()

                    # 异步触发飞书同步（全量同步，防重确保只新增未同步记录）
                    try:
                        import threading
                        from sync_history_to_feishu import migrate_garment_consumption_to_feishu
                        threading.Thread(target=migrate_garment_consumption_to_feishu).start()
                    except Exception:
                        pass

                    st.success(f"✅ 已记录：{sel_fac} 消耗了 {qty} 个 {bag_n}")
                except Exception as e:
                    st.error(f"保存失败: {e}")
                finally:
                    conn.close()