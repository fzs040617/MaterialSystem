import streamlit as st
import sys
import os

# 将当前目录添加到系统路径，确保子模块导入正常
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import init_db, force_fix_db_schema
from business_logic import login_user, register_user, clear_user_draft
from config import INVITATION_CODE

# 导入各功能模块
from modules.dashboard import render_dashboard
from modules.purchase import render_purchase_order
from modules.outbound import render_outbound, render_other_material_outbound
from modules.accessory import render_accessory_order
from modules.inbound import render_inbound, render_garment_consumption
from modules.history import render_history
from modules.admin import render_admin
from modules.crossborder import render_crossborder
from modules.feishu_portal import show_feishu_sync

# 1. 初始化系统环境
st.set_page_config(page_title="润微物料管理系统", layout="wide", page_icon="📦")
init_db()
force_fix_db_schema()

# 2. 会话状态管理
if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'username' not in st.session_state: st.session_state['username'] = ''
if 'role' not in st.session_state: st.session_state['role'] = ''

# 3. 登录与注册界面
def login_page():
    st.markdown("<h1 style='text-align: center;'>🔐 物料系统登录</h1>", unsafe_allow_html=True)
    t1, t2 = st.tabs(["登录", "注册"])
    with t1:
        u = st.text_input("账号")
        p = st.text_input("密码", type="password")
        if st.button("登录", type="primary", use_container_width=True):
            role = login_user(u, p)
            if role:
                st.session_state.update({'logged_in': True, 'username': u, 'role': role})
                st.rerun()
            else: st.error("账号或密码错误")
    with t2:
        nu = st.text_input("新账号")
        np = st.text_input("新密码", type="password")
        nr = st.selectbox("角色", ["sales", "warehouse"])
        ic = st.text_input("邀请码")
        if st.button("注册", use_container_width=True):
            if ic != INVITATION_CODE: st.error("邀请码错误")
            elif register_user(nu, np, nr): st.success("注册成功，请登录"); st.rerun()

# 4. 主程序路由
def main():
    role = st.session_state['role']
    name = st.session_state['username']
    
    with st.sidebar:
        st.info(f"👤 用户: {name} | 权限: {role}")
        if st.button("🚪 退出登录"):
            st.session_state['logged_in'] = False
            st.rerun()
            
    st.title("📦 智能物料管理系统")
    
    # 根据权限展示不同的 Tab
    if role == 'admin':
        tabs = st.tabs(["📊 看板", "🛒 采购合同下单", "📦 其他物料下单", "📋 包装袋下单", "🖨️ 辅料下单", "📥 入库", "👕 制衣厂消耗", "🌍 跨境物料", "📥 飞书采购申请", "📜 历史", "⚙️ 后台"])
        with tabs[0]: render_dashboard()
        with tabs[1]: render_purchase_order(name)
        with tabs[2]: render_other_material_outbound(name)
        with tabs[3]: render_outbound(name)
        with tabs[4]: render_accessory_order(name)
        with tabs[5]: render_inbound(name)
        with tabs[6]: render_garment_consumption(name)
        with tabs[7]: render_crossborder(name)
        with tabs[8]: show_feishu_sync(name)   # 添加 name 参数 
        with tabs[9]: render_history(role, name)
        with tabs[10]: render_admin()
    elif role == 'sales':
        tabs = st.tabs(["📊 看板", "🛒 采购合同下单", "📦 其他物料下单", "📋 包装袋下单", "🖨️ 辅料下单", "👕 制衣厂消耗", "🌍 跨境物料", "📥 飞书采购申请", "📜 历史"])
        # ... 对应渲染函数 ...
        with tabs[0]: render_dashboard()
        with tabs[1]: render_purchase_order(name)
        with tabs[2]: render_other_material_outbound(name)
        with tabs[3]: render_outbound(name)
        with tabs[4]: render_accessory_order(name)
        with tabs[5]: render_garment_consumption(name)
        with tabs[6]: render_crossborder(name)
        with tabs[7]: show_feishu_sync(name) 
        with tabs[8]: render_history(role, name)
    else:
        tabs = st.tabs(["📊 看板", "📥 入库", "📜 历史"])
        with tabs[0]: render_dashboard()
        with tabs[1]: render_inbound(name)
        with tabs[2]: render_history(role, name)

if st.session_state['logged_in']:
    main()
else:
    login_page()