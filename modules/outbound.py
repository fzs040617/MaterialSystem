import sys
import os
import threading
# [核心补丁] 强制让 Python 知道上一层目录在哪
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import datetime
import json
import time
import os

# 导入自定义模块
from database import get_db_conn, load_data
from business_logic import (
    auto_save_draft, clear_user_draft, 
    process_order_cart, process_other_material_cart, load_user_draft
)
from excel_engines import generate_cart_excel_bytes_multi, generate_other_mat_excel_bytes_multi
from sync_history_to_feishu import (
    migrate_order_history_to_feishu, 
    migrate_other_material_history_to_feishu
)


# ==========================================
# 1. 辅助 UI 组件：新建制衣厂弹窗
# ==========================================

@st.dialog("➕ 新建制衣厂档案")
def create_factory_dialog():
    """快捷新建制衣厂悬浮弹窗"""
    st.info("💡 填写完毕后将自动保存并选中，无需重新填写订单。")
    new_name = st.text_input("制衣厂名称 (必填) *")
    new_address = st.text_area("地址与联系方式", placeholder="例：广东省广州市海珠区新港西路135号 廖洁仪 13580417221")
    
    if st.button("💾 保存并自动勾选", type="primary", use_container_width=True):
        if not new_name.strip():
            st.error("❌ 制衣厂名称不能为空！")
            return
        conn = get_db_conn(); c = conn.cursor()
        try:
            c.execute("SELECT name FROM garment_factories WHERE name=%s", (new_name.strip(),))
            if c.fetchone():
                st.error("❌ 该制衣厂名称已存在！")
                return
            
            # 1. 本地数据库落盘
            c.execute("INSERT INTO garment_factories (name, address) VALUES (%s, %s)", (new_name.strip(), new_address.strip()))
            conn.commit()
            
            # ==========================================
            # 🌟 新增：异步推送到飞书多维表格 (绝对不卡前端)
            # ==========================================
            try:
                from sync_mysql_to_feishu import push_new_garment_factory_to_feishu
                threading.Thread(target=push_new_garment_factory_to_feishu, args=(new_name.strip(), new_address.strip())).start()
            except Exception as e:
                print(f"触发制衣厂飞书同步失败: {e}")
            # ==========================================

            # 用 session_state 传递给主页面进行自动勾选
            st.session_state['just_added_factory'] = new_name.strip()
            st.rerun()
        except Exception as e:
            st.error(f"数据库保存失败: {e}")
        finally:
            conn.close()

# ==========================================
# 2. 包装袋下单页面 (Pack Mat Outbound)
# ==========================================

def render_outbound(uname):
    # ==========================================
    # 1. 【核心修复】状态初始化区
    # 必须放在函数的第一行，防止 KeyError 报错
    # ==========================================
    if 'input_key' not in st.session_state:
        st.session_state['input_key'] = 0
    
    if 'editor_key' not in st.session_state:  # <--- 这就是之前报错的原因，现在补上了
        st.session_state['editor_key'] = 0
        
    if 'keep_prod_name' not in st.session_state:
        st.session_state['keep_prod_name'] = ""
        
    if 'keep_src_factory' not in st.session_state: # <--- 用于记忆工厂选择
        st.session_state['keep_src_factory'] = "—— 请选择 ——"

    # 获取当前的刷新 Key
    k = str(st.session_state['input_key'])
    placeholder = "—— 请选择 ——"

# === [新增: 智能加载清单草稿] ===
    if 'order_cart' not in st.session_state:
        draft_df, draft_time = load_user_draft(uname, 'pack_mat')
        if draft_df is not None and not draft_df.empty:
            # 将捞出来的表格数据还原成 List 字典格式给购物车用
            if 'date' in draft_df.columns:
                draft_df['date'] = pd.to_datetime(draft_df['date'], errors='coerce').dt.date
            st.session_state['order_cart'] = draft_df.to_dict('records')
            st.session_state['order_draft_time'] = draft_time
        else:
            st.session_state['order_cart'] = []
            st.session_state['order_draft_time'] = None

    if st.session_state.get('order_draft_time'):
        c_i, c_b = st.columns([4, 1])
        with c_i: st.info(f"💡 系统已恢复未提交的发货清单草稿 (时间: {st.session_state['order_draft_time']})")
        with c_b:
            if st.button("🗑️ 废弃当前草稿", key="clear_order_btn", use_container_width=True):
                clear_user_draft(uname, 'pack_mat')
                st.session_state['order_cart'] = []
                st.session_state['order_draft_time'] = None
                st.rerun()

    # ==========================================
    # 2. 页面布局
    # ==========================================
    st.header("📝 包装袋下单 (草稿箱模式)")
    c_meta1, c_meta2 = st.columns(2)
    md = c_meta1.date_input("下单日期", datetime.date.today())
    st.divider()

    col_table, col_ctrl = st.columns([1, 2])
    
    # ==========================================
    # 3. 右侧：操作控制台
    # ==========================================
    with col_ctrl:
        st.info("👇 **添加明细**")
        
        row1_c1, row1_c2 = st.columns(2)
        
        # --- [修复] 发货源工厂 (带记忆功能) ---
        df_pack = load_data("packaging_factories")
        if not df_pack.empty and 'factory_type' in df_pack.columns:
            # 核心过滤：只展示包装袋工厂
            raw_pack_facts = df_pack[df_pack['factory_type'].isin(['包装袋', None, ''])]['name'].tolist()
        else:
            raw_pack_facts = df_pack['name'].tolist() if not df_pack.empty else []
            
        pack_facts = [placeholder] + [f for f in raw_pack_facts if f]
        
        if len(pack_facts) <= 1:
            st.warning("请先去后台添加包装袋工厂")
            return
        
        # 计算默认选中项 (为了实现“保留上次选择”)
        default_fac_idx = 0
        last_fac = st.session_state['keep_src_factory']
        if last_fac in pack_facts:
            default_fac_idx = pack_facts.index(last_fac)
        
        # 这里的 key 必须包含 k，以便在需要时刷新，但 index 参数会让它停留在上次的位置
        src_factory = row1_c1.selectbox("🏭 发货源工厂", pack_facts, index=default_fac_idx, key=f"src_fac_{k}")
        
        # --- 销售平台 ---
        platforms = [placeholder, "唯品","天猫","抖音","淘宝","三野","京东","实体店","仓库"]
        mp_item = row1_c2.selectbox("销售平台", platforms, index=0, key=f"mp_{k}")

        # 1. 动态抓取历史商品名称 (使用更强大的 Pandas 引擎防错)
        df_hist = load_data("order_history")
        hist_names = []
        if not df_hist.empty and 'product_name' in df_hist.columns:
            # 提取并转换为文本，去掉前后误打的空格
            valid_names = df_hist['product_name'].dropna().astype(str).str.strip()
            # 过滤掉空字符串，并进行去重转化
            hist_names = valid_names[valid_names != ''].unique().tolist()
            
            # [诊断器] 如果你有测试需要，可以取消下一行的注释来看看抓到了多少个
            # st.caption(f"👀 底层抓取到 {len(hist_names)} 个独立商品名")

        # 2. 构建下拉框选项
        prod_options = ["(➕ 输入新商品)"] + hist_names
        
        # 3. 处理历史记忆定位
        default_prod = st.session_state.get('keep_prod_name', '')
        default_idx = 0
        if default_prod in prod_options:
            default_idx = prod_options.index(default_prod)
        elif default_prod != '':
            # 如果记忆的是个全新的名字（还没正式写进数据库），临时插入列表中
            prod_options.insert(1, default_prod)
            default_idx = 1

        # 4. 渲染可搜索的下拉框
        sel_prod = st.selectbox("商品名称 (支持输入搜索)", prod_options, index=default_idx, key=f"prod_sel_{k}")
        
        # 5. 判定结果：如果选了新增，就弹出真正的输入框
        # 5. 【联动 UI】如果选了新增，才展现输入框
        if sel_prod == "(➕ 输入新商品)":
            raw_new_prod = st.text_input("✨ 请输入新商品名称", placeholder="必填", key=f"prod_new_{k}")
            mprod_item = str(raw_new_prod).strip() # 强制去除前后不小心的空格
            
            # [新增防呆设计] 实时检测重复
            if mprod_item and mprod_item in hist_names:
                st.info(f"💡 提示：【{mprod_item}】已经在历史库中存在了，您可以继续下单，系统会自动将其合并，不会产生重复项！")
        else:
            mprod_item = sel_prod
        
        # --- 包装袋选择逻辑 ---
        df_inv = load_data("inventory")
        # 只有当工厂选了具体的值，才去查库存
        if src_factory != placeholder:
            df_src_inv = df_inv[df_inv['factory_name'] == src_factory]
        else:
            df_src_inv = pd.DataFrame() # 空表
        
        bag_name, bag_size, real_stock, avail_stock = "", "", 0, 0
        bag_sel = placeholder # 默认值

        if src_factory == placeholder:
            st.caption("💡 请先选择源工厂以加载对应库存")
            st.selectbox("包装袋规格", [placeholder], disabled=True, key=f"bag_disabled_{k}")
        elif df_src_inv.empty:
            st.error(f"该工厂暂无库存记录")
            st.selectbox("包装袋规格", [placeholder], disabled=True, key=f"bag_empty_{k}")
        else:
            # 构建选项
            df_src_inv['opt'] = df_src_inv['bag_name'] + " | " + df_src_inv['bag_size']
            inv_map = df_src_inv.set_index('opt')['stock_quantity'].to_dict()
            
            # 排序逻辑
            bag_specs_df = load_data("bag_specs")
            sort_map = {}
            if not bag_specs_df.empty and 'sort_order' in bag_specs_df.columns:
                bag_specs_df['sort_order'] = bag_specs_df['sort_order'].fillna(0).astype(int)
                sort_map = dict(zip(bag_specs_df['name'] + " | " + bag_specs_df['size'], bag_specs_df['sort_order']))

            raw_options = list(inv_map.keys())
            # 排序：权重大的在前，0排最后
            sorted_options = sorted(raw_options, key=lambda x: (sort_map.get(x, 0) if sort_map.get(x, 0) > 0 else float('inf'), x))
            
            bag_options = [placeholder] + sorted_options
            bag_sel = st.selectbox("包装袋规格", bag_options, index=0, key=f"bag_sel_{k}")

            # 选中了具体的袋子
            if bag_sel != placeholder:
                real_stock = inv_map.get(bag_sel, 0)
                b_parts = bag_sel.split(" | ")
                bag_name, bag_size = b_parts[0], b_parts[1]
                
                # 图片预览
                conn = get_db_conn()
                c = conn.cursor()
                c.execute("SELECT image_path FROM bag_specs WHERE name=%s AND size=%s", (bag_name, bag_size))
                img_res = c.fetchone()
                conn.close()
                if img_res and img_res[0] and os.path.exists(img_res[0]):
                    st.image(img_res[0], width=200, caption=f"预览: {bag_name}")
                
                # 计算可用库存
                used_in_cart = 0
                if 'order_cart' in st.session_state:
                    for item in st.session_state['order_cart']:
                        if (item['src_factory'] == src_factory and 
                            item['bag_name'] == bag_name and 
                            item['bag_size'] == bag_size):
                            used_in_cart += item['qty']
                
                avail_stock = real_stock - used_in_cart
                if avail_stock < 0: avail_stock = 0 # 避免显示负数
                
                st.metric("当前可用", f"{avail_stock:,}", f"总库存 {real_stock}", delta_color="normal")

        qty_input = st.number_input("数量", min_value=0, step=100, key=f"qty_{k}")
        add_btn = st.button("➕ 加入清单", type="primary", use_container_width=True)

# ==========================================
    # 4. 左侧：制衣厂勾选 (支持悬浮窗新建 + 自动打勾)
    # ==========================================
    with col_table:
        # 使用两列布局，把标题和新建按钮放在同一行，美观紧凑
        col_t1, col_t2 = st.columns([2, 1])
        col_t1.subheader("🚚 勾选接收制衣厂")
        if col_t2.button("➕ 新增制衣厂", use_container_width=True):
            create_factory_dialog()

        df_g = load_data("garment_factories")
        if df_g.empty:
            st.warning("⚠️ 无制衣厂数据，请点击上方按钮新建")
        else:
            factory_list = df_g['name'].tolist()
            
            # --- [核心黑科技] 拦截弹窗刚刚新建的制衣厂，并强行自动选中 ---
            search_key = f"search_fac_{st.session_state['editor_key']}"
            if 'just_added_factory' in st.session_state:
                new_fac = st.session_state['just_added_factory']
                if new_fac in factory_list:
                    if search_key not in st.session_state:
                        st.session_state[search_key] = [new_fac]
                    elif new_fac not in st.session_state[search_key]:
                        # 将新厂合并到当前已选的列表中
                        st.session_state[search_key] = st.session_state[search_key] + [new_fac]
                # 用完立刻销毁接力棒
                del st.session_state['just_added_factory']

            # 1. 顶部：快捷搜索组件 (会自动读取上面的 session_state 状态)
            quick_search = st.multiselect(
                "🔍 快速搜索 (选中的厂会自动在下方表格打勾)",
                options=factory_list,
                key=search_key,
                placeholder="输入关键字快速查找..."
            )
            
            # 2. 组装表格数据
            df_display = df_g[['name']].copy()
            # 利用 isin()，如果该厂在上面的搜索框里（包括我们刚刚自动塞进去的新厂），✅ 默认就是 True
            df_display.insert(0, "✅", df_display['name'].isin(quick_search))

            # 3. 渲染大家熟悉的打勾表格
            edited = st.data_editor(
                df_display,
                key=f"editor_{st.session_state['editor_key']}", 
                column_config={"✅": st.column_config.CheckboxColumn("选", width="small")},
                hide_index=True, 
                use_container_width=True, 
                height=400
            )
            
            # ==========================================
            # 5. 提交逻辑
            # ==========================================
            if add_btn:
                # 最终结果完全以表格里真实打勾的状态为准（无论它是搜索选的还是鼠标点的）
                selected_rows = edited[edited["✅"] == True]
                
                # 严格校验
                if src_factory == placeholder: st.error("❌ 请选择发货源工厂")
                elif mp_item == placeholder: st.error("❌ 请选择销售平台")
                elif bag_sel == placeholder: st.error("❌ 请选择包装袋规格")
                elif selected_rows.empty: st.error("❌ 请勾选制衣厂")
                elif not mprod_item: st.error("❌ 请填写商品名称")
                elif qty_input <= 0: st.error("❌ 数量需大于0")
                else:
                    total_needed = len(selected_rows) * qty_input
                    if total_needed > avail_stock:
                        st.error(f"库存不足！当前可用 {avail_stock}，但总需求为 {total_needed}")
                    else:
                        if 'order_cart' not in st.session_state: st.session_state['order_cart'] = []
                        
                        # 写入购物车
                        for _, row in selected_rows.iterrows():
                            st.session_state['order_cart'].append({
                                "src_factory": src_factory,
                                "platform": mp_item,
                                "product_name": mprod_item,
                                "bag_name": bag_name,
                                "bag_size": bag_size,
                                "dst_garment": row['name'],
                                "qty": qty_input,
                                "date": md 
                            })
                        
                        # --- [核心] 重置与记忆逻辑 ---
                        st.session_state['keep_prod_name'] = mprod_item    # 1. 记住商品名
                        st.session_state['keep_src_factory'] = src_factory # 2. 记住工厂 (用户要求的)
                        
                        st.session_state['input_key'] += 1   # 3. 拨动开关，重置其他下拉框
                        st.session_state['editor_key'] += 1  # 4. 重置左侧勾选框
                        
                        auto_save_draft(uname, 'pack_mat', pd.DataFrame(st.session_state['order_cart']))

                        st.toast(f"✅ 已成功添加 {len(selected_rows)} 条明细", icon="🛒")
                        st.rerun()

    # ==========================================
    # 6. 草稿箱与下载
    # ==========================================
    # ==========================================
    # 6. 草稿箱与下载 (全新容错版)
    # ==========================================
    if 'order_cart' in st.session_state and st.session_state['order_cart']:
        st.divider()
        st.subheader("🛒 待发货清单 (草稿箱)")
        
        # 将 Session 里的数据转为 DataFrame
        cart_df = pd.DataFrame(st.session_state['order_cart'])
        
        # [核心] 在最前面插入一个用于勾选的布尔列
        cart_df.insert(0, "🗑️ 选中删除", False)
        
        # 汉化表头，提升可读性
        disp_cols = {
            "🗑️ 选中删除": "🗑️ 选中删除", 
            "src_factory": "源工厂", 
            "platform": "平台", 
            "product_name": "商品", 
            "bag_name": "包装袋", 
            "bag_size": "尺寸", 
            "dst_garment": "收货制衣厂", 
            "qty": "数量"
        }
        cart_df_disp = cart_df.rename(columns=disp_cols)
        
        # 使用 data_editor 渲染，禁用除“选中删除”外的所有列的编辑权限（防篡改库存）
        edited_cart = st.data_editor(
            cart_df_disp[list(disp_cols.values())],
            column_config={
                "🗑️ 选中删除": st.column_config.CheckboxColumn("选错可删", default=False),
                "源工厂": st.column_config.TextColumn(disabled=True),
                "平台": st.column_config.TextColumn(disabled=True),
                "商品": st.column_config.TextColumn(disabled=True),
                "包装袋": st.column_config.TextColumn(disabled=True),
                "尺寸": st.column_config.TextColumn(disabled=True),
                "收货制衣厂": st.column_config.TextColumn(disabled=True),
                "数量": st.column_config.NumberColumn(disabled=True),
            },
            hide_index=True,
            use_container_width=True,
            key=f"cart_editor_{st.session_state['input_key']}" # 绑定动态 key 保证刷新
        )
        
        # 重新排版按钮区
        c_submit1, c_submit2, c_submit3 = st.columns([1.5, 1.5, 4])
        with c_submit1:
            if st.button("🗑️ 清空全部", type="secondary", use_container_width=True):
                st.session_state['order_cart'] = []; st.rerun()
                
        with c_submit2:
            if st.button("✂️ 删除选中明细", type="secondary", use_container_width=True):
                # 找出被勾选的行的索引
                rows_to_delete = edited_cart[edited_cart["🗑️ 选中删除"] == True].index.tolist()
                
                if rows_to_delete:
                    # 保留没有被勾选的行
                    st.session_state['order_cart'] = [
                        item for i, item in enumerate(st.session_state['order_cart']) if i not in rows_to_delete
                    ]
                    st.session_state['input_key'] += 1
                    
                    # 只要删除了物料，立刻自动更新【包装袋】的草稿
                    auto_save_draft(uname, 'pack_mat', pd.DataFrame(st.session_state['order_cart']))
                    st.rerun()
                else:
                    st.warning("⚠️ 请先在上方表格的【🗑️ 选中删除】列勾选你要删除的明细！")
                    
        with c_submit3:
            if st.button("🚀 确认并提交正式订单", type="primary", use_container_width=True):
                meta = {"date": md}
                file_data, file_name, file_mime = generate_cart_excel_bytes_multi(st.session_state['order_cart'], meta, uname)
                ok, msg = process_order_cart(st.session_state['order_cart'], meta, uname)
                if ok:
                    # 🌟 新增：包装袋数据落盘后，立刻开启后台线程同步飞书
                    try:
                        sync_thread = threading.Thread(target=migrate_order_history_to_feishu)
                        sync_thread.start()
                    except Exception as e:
                        pass
                        
                    # [修复1] 存入正确的 final_file，这样页面底部的下载按钮才会弹出来！
                    st.session_state['final_file'] = {"data": file_data, "name": file_name, "mime": file_mime}
                    
                    # [修复2] 清空的是包装袋的购物车，而不是 omat_cart！
                    st.session_state['order_cart'] = []
                    
                    # [修复3] 正式发货了，撕毁包装袋草稿！
                    clear_user_draft(uname, 'pack_mat')
                    st.session_state['order_draft_time'] = None # 变量名必须对齐
                    st.rerun()
                else: st.error(msg)

    if 'final_file' in st.session_state:
        st.success("🎉 下单成功！点击下方按钮下载单据：")
        st.download_button("📥 下载出货单 (Excel/Zip)", 
                           st.session_state['final_file']['data'], 
                           st.session_state['final_file']['name'], 
                           st.session_state['final_file']['mime'],
                           type="primary")
        if st.button("继续录入下一单", key="btn_next_bag"):
            del st.session_state['final_file']
            st.rerun()

# ==========================================
# 3. 其他物料下单页面 (Other Material Outbound)
# ==========================================

def render_other_material_outbound(uname):
    if 'omat_input_key' not in st.session_state: st.session_state['omat_input_key'] = 0
    if 'omat_editor_key' not in st.session_state: st.session_state['omat_editor_key'] = 0
    if 'omat_keep_src' not in st.session_state: st.session_state['omat_keep_src'] = "—— 请选择 ——"

    k = str(st.session_state['omat_input_key'])
    placeholder = "—— 请选择 ——"

# === [新增: 智能加载清单草稿] ===
    if 'omat_cart' not in st.session_state:
        draft_df, draft_time = load_user_draft(uname, 'other_mat')
        if draft_df is not None and not draft_df.empty:
            # 将捞出来的表格数据还原成 List 字典格式给购物车用
            if 'date' in draft_df.columns:
                draft_df['date'] = pd.to_datetime(draft_df['date'], errors='coerce').dt.date
            st.session_state['omat_cart'] = draft_df.to_dict('records')
            st.session_state['omat_draft_time'] = draft_time
        else:
            st.session_state['omat_cart'] = []
            st.session_state['omat_draft_time'] = None

    if st.session_state.get('omat_draft_time'):
        c_i, c_b = st.columns([4, 1])
        with c_i: st.info(f"💡 系统已恢复未提交的发货清单草稿 (时间: {st.session_state['omat_draft_time']})")
        with c_b:
            if st.button("🗑️ 废弃当前草稿", key="clear_omat_btn", use_container_width=True):
                clear_user_draft(uname, 'other_mat')
                st.session_state['omat_cart'] = []
                st.session_state['omat_draft_time'] = None
                st.rerun()

    st.header("📦 其他物料下单 (草稿箱模式)")
    c_meta1, c_meta2 = st.columns(2)
    md = c_meta1.date_input("下单日期", datetime.date.today(), key="omat_date")
    st.divider()

    col_table, col_ctrl = st.columns([1, 2])
    
    with col_ctrl:
        st.info("👇 **添加物料明细**")
        row1_c1, row1_c2 = st.columns(2)
        
        # 1. 过滤出发货源工厂 (只取“其他物料”类型)
        df_pack = load_data("packaging_factories")
        if not df_pack.empty and 'factory_type' in df_pack.columns:
            raw_pack_facts = df_pack[df_pack['factory_type'] == '其他物料']['name'].tolist()
        else:
            raw_pack_facts = []
            
        pack_facts = [placeholder] + [f for f in raw_pack_facts if f]
        default_fac_idx = pack_facts.index(st.session_state['omat_keep_src']) if st.session_state['omat_keep_src'] in pack_facts else 0
        src_factory = row1_c1.selectbox("🏭 发货源工厂 (乙方)", pack_facts, index=default_fac_idx, key=f"omat_src_{k}")
        
        # 2. 抓取物料主数据 (编码 + 名称)
        df_mat = load_data("material_master")
        mat_options = [placeholder]
        if not df_mat.empty:
            df_mat['display'] = df_mat['material_code'] + " | " + df_mat['product_name']
            mat_options += df_mat['display'].tolist()
            
        sel_mat = row1_c2.selectbox("📦 物料名称 (从主数据库拉取)", mat_options, key=f"omat_mat_{k}")
        qty_input = st.number_input("数量", min_value=0, step=100, key=f"omat_qty_{k}")
        add_btn = st.button("➕ 加入发货清单", type="primary", use_container_width=True)

    with col_table:
        st.subheader("🚚 勾选接收制衣厂")
        df_g = load_data("garment_factories")
        if df_g.empty:
            st.warning("⚠️ 无制衣厂数据")
            selected_rows = pd.DataFrame()
        else:
            factory_list = df_g['name'].tolist()
            quick_search = st.multiselect("🔍 快速搜索", options=factory_list, default=[], key=f"omat_search_{st.session_state['omat_editor_key']}")
            df_display = df_g[['name']].copy()
            df_display.insert(0, "✅", df_display['name'].isin(quick_search))
            edited = st.data_editor(df_display, key=f"omat_editor_{st.session_state['omat_editor_key']}", column_config={"✅": st.column_config.CheckboxColumn("选", width="small")}, hide_index=True, use_container_width=True, height=400)
            selected_rows = edited[edited["✅"] == True]

            if add_btn:
                if src_factory == placeholder: st.error("❌ 请选择发货源工厂")
                elif sel_mat == placeholder: st.error("❌ 请选择物料")
                elif selected_rows.empty: st.error("❌ 请勾选制衣厂")
                elif qty_input <= 0: st.error("❌ 数量需大于0")
                else:
                    if 'omat_cart' not in st.session_state: st.session_state['omat_cart'] = []
                    for _, row in selected_rows.iterrows():
                        st.session_state['omat_cart'].append({
                            "src_factory": src_factory, "material": sel_mat,
                            "dst_garment": row['name'], "qty": qty_input, "date": md 
                        })
                    # ... 你的原代码 ...
                    st.session_state['omat_keep_src'] = src_factory
                    st.session_state['omat_input_key'] += 1
                    st.session_state['omat_editor_key'] += 1
                    
                    # [新增] 只要加入了新物料，立刻自动存草稿
                    auto_save_draft(uname, 'other_mat', pd.DataFrame(st.session_state['omat_cart']))
                    st.toast(f"✅ 已成功添加 {len(selected_rows)} 条明细", icon="📦")
                    st.rerun()

    # 草稿箱逻辑
    if 'omat_cart' in st.session_state and st.session_state['omat_cart']:
        st.divider()
        st.subheader("🛒 其他物料待发货清单 (草稿箱)")
        cart_df = pd.DataFrame(st.session_state['omat_cart'])
        cart_df.insert(0, "🗑️ 选中删除", False)
        disp_cols = {"🗑️ 选中删除": "🗑️ 选中删除", "src_factory": "源工厂", "material": "物料信息", "dst_garment": "收货制衣厂", "qty": "数量"}
        cart_df_disp = cart_df.rename(columns=disp_cols)
        
        edited_cart = st.data_editor(
            cart_df_disp[list(disp_cols.values())],
            column_config={"🗑️ 选中删除": st.column_config.CheckboxColumn("选错可删", default=False)},
            disabled=["源工厂", "物料信息", "收货制衣厂", "数量"],
            hide_index=True, use_container_width=True, key=f"omat_cart_editor_{st.session_state['omat_input_key']}"
        )
        
        c_submit1, c_submit2, c_submit3 = st.columns([1.5, 1.5, 4])
        with c_submit1:
            if st.button("🗑️ 清空全部", type="secondary", use_container_width=True, key="omat_clear"):
                st.session_state['omat_cart'] = []; st.rerun()
        with c_submit2:
            if st.button("✂️ 删除选中明细", type="secondary", use_container_width=True, key="omat_del"):
                rows_to_delete = edited_cart[edited_cart["🗑️ 选中删除"] == True].index.tolist()
                if rows_to_delete:
                    st.session_state['omat_cart'] = [item for i, item in enumerate(st.session_state['omat_cart']) if i not in rows_to_delete]
                    st.session_state['omat_input_key'] += 1; st.rerun()
        with c_submit3:
            if st.button("🚀 确认并提交正式发货单", type="primary", use_container_width=True, key="omat_submit"):
                meta = {"date": md}
                file_data, file_name, file_mime = generate_other_mat_excel_bytes_multi(st.session_state['omat_cart'], meta, uname)
                ok, msg = process_other_material_cart(st.session_state['omat_cart'], meta, uname)
                if ok:
                    # 🌟 新增：其他物料数据落盘后，立刻开启后台线程同步飞书
                    try:
                        sync_thread = threading.Thread(target=migrate_other_material_history_to_feishu)
                        sync_thread.start()
                    except Exception as e:
                        pass
                        
                    st.session_state['omat_final_file'] = {"data": file_data, "name": file_name, "mime": file_mime}
                    st.session_state['omat_cart'] = []

                    clear_user_draft(uname, 'other_mat')
                    st.session_state['omat_draft_time'] = None

                    st.rerun()
                else: st.error(msg)

    if 'omat_final_file' in st.session_state:
        st.success("🎉 下单成功！点击下方按钮下载出货单：")
        st.download_button("📥 下载物料出货单", st.session_state['omat_final_file']['data'], st.session_state['omat_final_file']['name'], type="primary")
        if st.button("继续录入下一单", key="btn_next_omat"): del st.session_state['omat_final_file']; st.rerun()
