import streamlit as st
import pandas as pd
import datetime
import os
import base64
from database import get_db_conn

def render_crossborder(uname):
    st.header("🌍 跨境物料管理")
    st.caption("选择物料，加入清单，最后一次性提交。系统将自动扣减库存。")
    
    # 初始化 session_state
    if 'crossborder_cart' not in st.session_state:
        st.session_state['crossborder_cart'] = []
    if 'crossborder_cart_key' not in st.session_state:
        st.session_state['crossborder_cart_key'] = 0
    
    # 获取所有物料
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, material_code, name_model, image_path, stock_quantity
        FROM crossborder_materials_v2
        ORDER BY name_model
    """)
    materials = cursor.fetchall()
    conn.close()
    
    if not materials:
        st.warning("暂无跨境物料数据，请联系管理员添加。")
        return
    
    # 构建物料选项字典
    material_options = {}
    for m in materials:
        mat_id, code, name_model, img_path, stock = m
        display = f"{name_model} (编码: {code if code else '无'}) - 库存: {stock}"
        material_options[display] = {
            "id": mat_id,
            "code": code,
            "name_model": name_model,
            "img_path": img_path,
            "stock": stock
        }
    
    # 添加物料到购物车的界面
    with st.container():
        col1, col2 = st.columns([2, 1])
        with col1:
            selected_display = st.selectbox("📦 选择物料", list(material_options.keys()), key=f"cb_select_{st.session_state['crossborder_cart_key']}")
            selected = material_options[selected_display]
        with col2:
            qty = st.number_input("数量", min_value=1, step=1, value=1, key=f"cb_qty_{st.session_state['crossborder_cart_key']}")
        
        # 显示图片预览
        if selected["img_path"] and os.path.exists(selected["img_path"]):
            st.image(selected["img_path"], width=150, caption=selected["name_model"])
        else:
            st.caption("暂无图片")
        
        if st.button("➕ 加入清单", type="primary"):
            if qty > selected["stock"]:
                st.error(f"库存不足！当前库存仅 {selected['stock']} 件")
            else:
                st.session_state['crossborder_cart'].append({
                    "material_id": selected["id"],
                    "material_code": selected["code"],
                    "name_model": selected["name_model"],
                    "quantity": qty,
                    "stock_before": selected["stock"],
                    "img_path": selected["img_path"]
                })
                st.success(f"已添加 {selected['name_model']} x {qty}")
                st.rerun()
    
    # 显示购物车（草稿箱）
    if st.session_state['crossborder_cart']:
        st.divider()
        st.subheader("🛒 待提交清单 (草稿箱)")
        
        cart_df = pd.DataFrame(st.session_state['crossborder_cart'])
        cart_df.insert(0, "🗑️ 删除", False)
        display_cols = ["🗑️ 删除", "name_model", "material_code", "quantity"]
        display_df = cart_df[display_cols].rename(columns={
            "name_model": "物料名称型号",
            "material_code": "物料编码",
            "quantity": "数量"
        })
        
        edited_cart = st.data_editor(
            display_df,
            column_config={
                "🗑️ 删除": st.column_config.CheckboxColumn("勾选删除", default=False),
                "物料名称型号": st.column_config.TextColumn(disabled=True),
                "物料编码": st.column_config.TextColumn(disabled=True),
                "数量": st.column_config.NumberColumn(disabled=True)
            },
            hide_index=True,
            use_container_width=True,
            key="cb_cart_editor"
        )
        
        col_clear, col_submit = st.columns([1, 4])
        with col_clear:
            if st.button("🗑️ 清空清单", type="secondary"):
                st.session_state['crossborder_cart'] = []
                st.rerun()
        with col_submit:
            to_delete = edited_cart[edited_cart["🗑️ 删除"] == True].index.tolist()
            if to_delete:
                if st.button("✂️ 删除选中项", type="secondary"):
                    new_cart = [item for i, item in enumerate(st.session_state['crossborder_cart']) if i not in to_delete]
                    st.session_state['crossborder_cart'] = new_cart
                    st.rerun()
        
        if st.button("🚀 确认并提交所有订单", type="primary"):
            if not st.session_state['crossborder_cart']:
                st.warning("清单为空，无法提交")
            else:
                conn = get_db_conn()
                cursor = conn.cursor()
                success_count = 0
                fail_count = 0
                try:
                    for item in st.session_state['crossborder_cart']:
                        mat_id = item["material_id"]
                        qty = item["quantity"]
                        cursor.execute("SELECT stock_quantity FROM crossborder_materials_v2 WHERE id=%s", (mat_id,))
                        current_stock = cursor.fetchone()[0]
                        if qty > current_stock:
                            fail_count += 1
                            st.error(f"物料 {item['name_model']} 库存不足（当前{current_stock}，需要{qty}），已跳过")
                            continue
                        cursor.execute("UPDATE crossborder_materials_v2 SET stock_quantity = stock_quantity - %s WHERE id=%s", (qty, mat_id))
                        cursor.execute(
                            "INSERT INTO crossborder_orders_v2 (material_id, quantity, operator) VALUES (%s, %s, %s)",
                            (mat_id, qty, uname)
                        )
                        success_count += 1
                    conn.commit()
                    # 异步触发飞书全量同步
                    try:
                        import threading
                        from sync_history_to_feishu import migrate_crossborder_orders_to_feishu
                        threading.Thread(target=migrate_crossborder_orders_to_feishu).start()
                    except Exception:
                        pass
                    st.success(f"✅ 提交完成！成功 {success_count} 项，失败 {fail_count} 项。")
                    st.session_state['crossborder_cart'] = []
                    st.session_state['crossborder_cart_key'] += 1
                    st.rerun()
                except Exception as e:
                    conn.rollback()
                    st.error(f"提交失败: {e}")
                finally:
                    conn.close()
    
    # 显示库存列表（带图片，类似看板）
    st.divider()
    st.subheader("📋 当前物料库存列表")
    conn = get_db_conn()
    df_materials = pd.read_sql_query("SELECT material_code, name_model, image_path, stock_quantity FROM crossborder_materials_v2 ORDER BY name_model", conn)
    conn.close()
    if not df_materials.empty:
        def get_image_base64(path):
            if pd.isna(path) or not path or not os.path.exists(path):
                return None
            try:
                with open(path, "rb") as f:
                    b64_str = base64.b64encode(f.read()).decode()
                    return f"data:image/png;base64,{b64_str}"
            except:
                return None
        df_materials['图片'] = df_materials['image_path'].apply(get_image_base64)
        display_df = df_materials[['图片', 'material_code', 'name_model', 'stock_quantity']]
        st.dataframe(
            display_df,
            column_config={
                "图片": st.column_config.ImageColumn("图片", width="small"),
                "material_code": "物料编码",
                "name_model": "名称型号",
                "stock_quantity": st.column_config.NumberColumn("库存", format="%d")
            },
            hide_index=True,
            use_container_width=True
        )
    else:
        st.info("暂无物料")