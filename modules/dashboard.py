import streamlit as st
import pandas as pd
import plotly.express as px
import base64
import os
import threading
import json
from database import get_db_conn, load_data
from sync_mysql_to_feishu import sync_inventory_to_feishu
from utils import format_number_cn

st.write("DEBUG: 开始渲染")
def render_dashboard():
    # 加载数据
    df_h = load_data("order_history")
    df_i = load_data("inventory")
    tq = df_h['total_quantity'].sum() if not df_h.empty else 0
    ls = len(df_i[df_i['stock_quantity'] < 200]) if not df_i.empty else 0
    
    st.subheader("🏭 包装袋实时库存与消耗看板")
    
    if not df_i.empty or not df_h.empty:
        conn = get_db_conn()
        df_specs = pd.read_sql_query("SELECT name, size, image_path FROM bag_specs", conn)
        conn.close()
        
        # ----------------------------------------------------
        # 1. 整理当前库存与单价数据 (Stock & Price)
        # ----------------------------------------------------
        if not df_i.empty:
            # 库存透视
            df_stock = df_i.pivot_table(index=['bag_name', 'bag_size'], columns='factory_name', values='stock_quantity', aggfunc='sum', fill_value=0).reset_index()
            stock_facs = [c for c in df_stock.columns if c not in ['bag_name', 'bag_size']]
            df_stock.rename(columns={f: f"{f} 库存" for f in stock_facs}, inplace=True)
            
            # 单价透视
            df_price = df_i.pivot_table(index=['bag_name', 'bag_size'], columns='factory_name', values='unit_price', aggfunc='mean', fill_value=0.0).reset_index()
            df_price.rename(columns={f: f"{f} 单价" for f in stock_facs}, inplace=True)
            
            df_pivot = pd.merge(df_stock, df_price, on=['bag_name', 'bag_size'], how='outer')
        else:
            df_pivot = pd.DataFrame(columns=['bag_name', 'bag_size'])
            stock_facs = []

        # ----------------------------------------------------
        # 2. 解析并整理历史消耗数据 (Consume)
        # ----------------------------------------------------
        hist_records = []
        if not df_h.empty:
            for _, row in df_h.iterrows():
                b_name = str(row.get('bag_name', '')).strip()
                b_size = str(row.get('bag_size', '')).strip()
                details_str = row.get('details', '{}')
                try:
                    details = json.loads(details_str) if details_str else {}
                    for ship in details.get('shipping', []):
                        fac = str(ship.get('src_factory', '')).strip()
                        qty = int(ship.get('qty', 0))
                        if fac and b_name:
                            hist_records.append({
                                'bag_name': b_name,
                                'bag_size': b_size,
                                'consume_qty': qty,
                                'factory_name': fac
                            })
                except: 
                    continue
        
        df_hist = pd.DataFrame(hist_records)
        if not df_hist.empty:
            df_hist_pivot = df_hist.pivot_table(index=['bag_name', 'bag_size'], columns='factory_name', values='consume_qty', aggfunc='sum', fill_value=0).reset_index()
            consume_facs = [c for c in df_hist_pivot.columns if c not in ['bag_name', 'bag_size']]
            df_hist_pivot.rename(columns={f: f"{f} 消耗" for f in consume_facs}, inplace=True)
        else:
            df_hist_pivot = pd.DataFrame(columns=['bag_name', 'bag_size'])
            consume_facs = []

        # ----------------------------------------------------
        # 3. 完美拼合：总览宽表 (Outer Join)
        # ----------------------------------------------------
        all_facs = sorted(list(set(stock_facs + consume_facs)))
        
        if not df_pivot.empty and not df_hist_pivot.empty:
            df_combined = pd.merge(df_pivot, df_hist_pivot, on=['bag_name', 'bag_size'], how='outer').fillna(0)
        elif not df_pivot.empty:
            df_combined = df_pivot.copy()
        elif not df_hist_pivot.empty:
            df_combined = df_hist_pivot.copy()
        else:
            df_combined = pd.DataFrame(columns=['bag_name', 'bag_size'])

        # 兜底：确保所有工厂的“库存”、“消耗”和“单价”列都存在
        for fac in all_facs:
            if f"{fac} 库存" not in df_combined.columns: df_combined[f"{fac} 库存"] = 0
            if f"{fac} 消耗" not in df_combined.columns: df_combined[f"{fac} 消耗"] = 0
            if f"{fac} 单价" not in df_combined.columns: df_combined[f"{fac} 单价"] = 0.0

        # 计算系统总览数字
        if not df_combined.empty:
            df_combined['总库存'] = df_combined[[f"{fac} 库存" for fac in all_facs]].sum(axis=1)
            df_combined['历史总消耗'] = df_combined[[f"{fac} 消耗" for fac in all_facs]].sum(axis=1)
        else:
            df_combined['总库存'] = 0
            df_combined['历史总消耗'] = 0

        # 严格以现存规格为基准匹配
        df_merged = pd.merge(df_specs, df_combined, left_on=['name', 'size'], right_on=['bag_name', 'bag_size'], how='left')
        df_merged['bag_name'] = df_merged['name']
        df_merged['bag_size'] = df_merged['size']
        
        numeric_cols = ['总库存', '历史总消耗'] + [f"{fac} 库存" for fac in all_facs] + [f"{fac} 消耗" for fac in all_facs] + [f"{fac} 单价" for fac in all_facs]
        for col in numeric_cols:
            if col in df_merged.columns:
                df_merged[col] = df_merged[col].fillna(0)

        # ----------------------------------------------------
        # 4. 前端展示列重排与渲染
        # ----------------------------------------------------
        def get_image_base64(path):
            if pd.isna(path) or not path or not os.path.exists(path): return None
            try:
                with open(path, "rb") as f:
                    b64_str = base64.b64encode(f.read()).decode()
                    return f"data:image/png;base64,{b64_str}"
            except: return None
        
        df_merged['图片'] = df_merged['image_path'].apply(get_image_base64)
        
        # 排版顺序：基本信息 -> 库存 -> 消耗 -> 单价
        col_order = ['图片', 'bag_name', 'bag_size', '总库存']
        for fac in all_facs: col_order.append(f"{fac} 库存")
        col_order.append('历史总消耗')
        for fac in all_facs: col_order.append(f"{fac} 消耗")
        for fac in all_facs: col_order.append(f"{fac} 单价")
            
        df_display = df_merged[col_order].rename(columns={'bag_name': '名称', 'bag_size': '尺寸'})
        is_admin = st.session_state.get('role') == 'admin'

        if is_admin:
            column_config = {
                "图片": st.column_config.ImageColumn("预览图", help="包装袋样式"),
                "名称": st.column_config.TextColumn("名称", disabled=True),
                "尺寸": st.column_config.TextColumn("尺寸", disabled=True),
                "总库存": st.column_config.NumberColumn("总库存", format="%d", disabled=True, help="下方各工厂库存加总"),
                "历史总消耗": st.column_config.NumberColumn("历史总消耗", format="%d", disabled=True, help="该包装袋从系统上线至今的发出总数"),
            }
            # 配置动态工厂列
            for fac in all_facs:
                column_config[f"{fac} 库存"] = st.column_config.NumberColumn(f"{fac} 库存", format="%d", step=100)
                column_config[f"{fac} 消耗"] = st.column_config.NumberColumn(f"{fac} 消耗", format="%d", disabled=True)
                column_config[f"{fac} 单价"] = st.column_config.NumberColumn(f"{fac} 单价", format="%.3f", step=0.001)
            
            edited_df = st.data_editor(
                df_display,
                column_config=column_config,
                hide_index=True,
                use_container_width=True,
                height=700,
                key="inventory_editor_pivot"
            )

            if st.button("💾 保存修改并同步至飞书", type="primary"):
                conn = get_db_conn()
                cursor = conn.cursor()
                try:
                    update_count = 0
                    for idx, row in edited_df.iterrows():
                        bag_name = row['名称']
                        bag_size = row['尺寸']
                        original_row = df_display.iloc[idx] 
                        
                        for fac in all_facs:
                            stock_col = f"{fac} 库存"
                            price_col = f"{fac} 单价"
                            
                            new_qty = row[stock_col]
                            old_qty = original_row[stock_col]
                            new_price = row[price_col]
                            old_price = original_row[price_col]
                            
                            # 库存或单价任意一项发生变动，均触发数据库更新
                            if new_qty != old_qty or abs(new_price - old_price) > 0.0001:
                                cursor.execute("SELECT 1 FROM inventory WHERE factory_name=%s AND bag_name=%s AND bag_size=%s", (fac, bag_name, bag_size))
                                if cursor.fetchone():
                                    cursor.execute("""
                                        UPDATE inventory 
                                        SET stock_quantity = %s, unit_price = %s
                                        WHERE factory_name = %s AND bag_name = %s AND bag_size = %s
                                    """, (new_qty, new_price, fac, bag_name, bag_size))
                                else:
                                    cursor.execute("""
                                        INSERT INTO inventory (factory_name, bag_name, bag_size, stock_quantity, unit_price)
                                        VALUES (%s, %s, %s, %s, %s)
                                    """, (fac, bag_name, bag_size, new_qty, new_price))
                                update_count += 1
                    conn.commit()
                    
                    # 异步触发飞书库存全量同步
                    try:
                        threading.Thread(target=sync_inventory_to_feishu).start()
                    except Exception as e:
                        pass
                    
                    st.success(f"✅ 成功更新 {update_count} 处库存与单价数据！正在后台推送到飞书...")
                    st.balloons()
                    import time
                    time.sleep(1.5) 
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    conn.rollback()
                    st.error(f"保存失败: {e}")
                finally:
                    conn.close()
        else:
            column_config = {
                "图片": st.column_config.ImageColumn("预览图"),
                "名称": st.column_config.TextColumn("名称"),
                "尺寸": st.column_config.TextColumn("尺寸"),
                "总库存": st.column_config.NumberColumn("总库存", format="%d"),
                "历史总消耗": st.column_config.NumberColumn("历史总消耗", format="%d"),
            }
            for fac in all_facs:
                column_config[f"{fac} 库存"] = st.column_config.NumberColumn(f"{fac} 库存", format="%d")
                column_config[f"{fac} 消耗"] = st.column_config.NumberColumn(f"{fac} 消耗", format="%d")
                column_config[f"{fac} 单价"] = st.column_config.NumberColumn(f"{fac} 单价", format="%.3f")

            st.dataframe(df_display, column_config=column_config, hide_index=True, use_container_width=True, height=500)
        
        if ls > 0:
            with st.expander(f"⚠️ 发现 {ls} 项告急物料 (点击查看明细)"):
                st.dataframe(df_i[df_i['stock_quantity'] < 200][['factory_name','bag_name','bag_size','stock_quantity']], hide_index=True)
    else:
        st.info("📦 暂无库存或历史发货数据可供展示")
    
    # ================= 未完成采购合同（增强修复版） =================
    
    st.subheader("📋 未完成采购合同")
    
    @st.dialog("📄 采购合同详情", width="large")
    def show_po_detail(po_id):
        conn = get_db_conn()
        try:
            conn.commit() # 打破数据库快照，强制获取最新数据
            df_po = pd.read_sql_query(
                "SELECT id, contract_no, create_time, factory_name, operator, remark, status FROM purchase_orders WHERE id=%s",
                conn, params=(po_id,)
            )
            if df_po.empty:
                st.warning("未找到合同信息")
                return
            row = df_po.iloc[0]
            
            # 安全单号兜底
            display_no = str(row['contract_no']) if pd.notna(row['contract_no']) and str(row['contract_no']).strip() != '' else f"PO-{row['id']:04d}"
            
            st.markdown(f"**合同编号：** {display_no}")
            st.markdown(f"**创建时间：** {row['create_time']}")
            st.markdown(f"**乙方工厂：** {row['factory_name']}")
            st.markdown(f"**操作人：** {row['operator']}")
            st.markdown(f"**状态：** {'✅ 已完成' if str(row['status']).strip().lower() == 'completed' else '⏳ 未完成'}")
            
            # 兼容新旧 JSON 格式的解析
            try:
                raw_data = json.loads(row['remark']) if row['remark'] else []
                items = raw_data.get('items', []) if isinstance(raw_data, dict) else raw_data
                
                if items and isinstance(items, list) and len(items) > 0:
                    st.markdown("**📦 物料明细清单：**")
                    df_items = pd.DataFrame(items)
                    display_cols = ['物料编号', '物料名称', '材质', '颜色', '尺寸', '收货标准', '数量', '单位', '单价', '货期', '备注']
                    existing_cols = [col for col in display_cols if col in df_items.columns]
                    if existing_cols:
                        st.dataframe(df_items[existing_cols], hide_index=True, use_container_width=True)
                    else:
                        st.info("明细表格无支持显示的列")
                else:
                    st.info("暂无物料明细")
            except Exception as e:
                st.error("物料明细解析失败，数据格式可能有误")
        finally:
            conn.close()
            
        if st.button("关闭", type="primary"):
            del st.session_state['selected_po_id']
            st.rerun()
    
    conn = get_db_conn()
    try:
        # 强制执行 commit 打破长连接幻读，确保拿到 Navicat 里的最新状态
        conn.commit() 
        
        # 使用 TRIM 和 LOWER 免疫大小写和空格干扰
        df_po = pd.read_sql_query(
            "SELECT id, contract_no, factory_name, create_time, operator, remark, status FROM purchase_orders WHERE TRIM(LOWER(status))='pending' OR status IS NULL OR TRIM(status)='' ORDER BY create_time DESC",
            conn
        )
        
        if not df_po.empty:
            def get_delivery_date(remark_str):
                try:
                    js = json.loads(remark_str) if remark_str else []
                    items = js.get('items', []) if isinstance(js, dict) else js
                    if items and isinstance(items, list) and len(items) > 0:
                        delivery = items[0].get('货期')
                        if delivery:
                            return delivery
                    return '-'
                except:
                    return '-'
                    
            col_heads = st.columns([3, 2, 2, 2, 2, 1])
            col_heads[0].write("**合同编号**")
            col_heads[1].write("**工厂**")
            col_heads[2].write("**创建日期**")
            col_heads[3].write("**操作人**")
            col_heads[4].write("**货期**")
            col_heads[5].write("**操作**")
            
            for _, row in df_po.iterrows():
                cols = st.columns([3, 2, 2, 2, 2, 1])
                display_no = str(row['contract_no']) if pd.notna(row['contract_no']) and str(row['contract_no']).strip() != '' else f"PO-{row['id']:04d}"
                
                if cols[0].button(display_no, key=f"view_po_{row['id']}", use_container_width=False, type="tertiary"):
                    st.session_state['selected_po_id'] = row['id']
                    st.rerun()
                    
                cols[1].write(row['factory_name'])
                create_time_str = str(row['create_time'])[:10] if pd.notna(row['create_time']) else "-"
                cols[2].write(create_time_str)
                cols[3].write(row['operator'])
                cols[4].write(get_delivery_date(row['remark']))
                
                if cols[5].button("✅ 完成", key=f"complete_po_{row['id']}"):
                    conn2 = get_db_conn()
                    cur = conn2.cursor()
                    cur.execute("UPDATE purchase_orders SET status='completed' WHERE id=%s", (row['id'],))
                    conn2.commit()
                    cur.close()
                    conn2.close()
                    st.success(f"合同 {display_no} 已完成")
                    st.rerun()
        else:
            st.info("💡 暂无未完成的采购合同 (查询不到 pending 状态的记录)")
            
    except Exception as e:
        st.error(f"❌ 读取未完成合同列表时出错: {str(e)}")
    finally:
        conn.close()
    
    if 'selected_po_id' in st.session_state:
        show_po_detail(st.session_state['selected_po_id'])
    
    # ========== 包装袋各渠道发货占比 ==========
    st.subheader("🥧 包装袋各渠道发货占比")
    
    conn = get_db_conn()
    try:
        df_pie = pd.read_sql_query("SELECT order_date, platform, total_quantity FROM order_history", conn)
        if not df_pie.empty:
            df_pie['order_date'] = pd.to_datetime(df_pie['order_date'], errors='coerce')
            df_pie = df_pie.dropna(subset=['order_date'])
            df_pie['year_month'] = df_pie['order_date'].dt.strftime('%Y-%m')
            month_list = ["全部"] + sorted(df_pie['year_month'].unique().tolist(), reverse=True)
            sel_month = st.selectbox("📅 按月份筛选查看", month_list, key="dash_pie_month")
            df_filtered = df_pie if sel_month == "全部" else df_pie[df_pie['year_month'] == sel_month]
            if not df_filtered.empty:
                df_grouped = df_filtered.groupby('platform')['total_quantity'].sum().reset_index()
                fig = px.pie(df_grouped, names='platform', values='total_quantity', hole=0.4,
                             title=f"【{sel_month if sel_month != '全部' else '历史全部'}】 渠道发货占比")
                fig.update_traces(textposition='inside', textinfo='percent+label')
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info(f"💡 {sel_month} 暂无包装袋发货记录。")
        else:
            st.caption("暂无发货数据可供分析。")
    except Exception as e:
        st.error(f"饼图加载失败: {e}")
    finally:
        conn.close()
    
    st.divider()
    ct1, ct2 = st.columns([3,1])
    with ct1: st.subheader("📅 每日消耗走势")
    if not df_h.empty:
        df_h['disp'] = df_h['bag_name'] + " (" + df_h['bag_size'] + ")"
        bags = df_h['disp'].unique().tolist()
        with ct2: sb = st.selectbox("🔍 筛选", ["全部"]+bags)
        df_h['dt'] = pd.to_datetime(df_h['order_date'])
        src = df_h if sb=="全部" else df_h[df_h['disp']==sb]
        if not src.empty:
            tr = src.groupby('dt')['total_quantity'].sum().reset_index().sort_values('dt')
            tr['d'] = tr['dt'].dt.strftime('%Y-%m-%d')
            fig = px.line(tr, x='d', y='total_quantity', markers=True, title=f"消耗: {sb}")
            fig.update_xaxes(type='category')
            st.plotly_chart(fig, use_container_width=True)
        else: st.info("无数据")
    else: st.info("无历史数据")
    
    # ========== 运营概览（指标卡）放到最底部 ==========
    st.divider()
    st.header("📈 运营概览")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("总订单", f"{len(df_h):,} 单")
    k2.metric("物料总消耗", f"{format_number_cn(tq)}")
    k3.metric("库存告急", f"{ls}", delta="需补货" if ls>0 else "健康", delta_color="inverse")