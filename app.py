import streamlit as st
import streamlit.components.v1 as components
import sys
import os

def inject_login_keyboard_shortcuts():
    """登录页快捷键：上下箭头切换账号/密码输入框，Enter 提交登录表单"""
    components.html(
        """
        <script>
        function getVisibleLoginInputs() {
            const doc = window.parent.document;
            const inputs = Array.from(doc.querySelectorAll('input'));

            const visibleInputs = inputs.filter(el => {
                const rect = el.getBoundingClientRect();
                const style = window.parent.getComputedStyle(el);
                return (
                    rect.width > 0 &&
                    rect.height > 0 &&
                    style.display !== "none" &&
                    style.visibility !== "hidden" &&
                    !el.disabled
                );
            });

            // 登录页当前可见的账号、密码输入框
            return visibleInputs.slice(0, 2);
        }

        function setupLoginShortcuts() {
            const doc = window.parent.document;

            if (doc.body.dataset.loginShortcutBound === "1") {
                return;
            }
            doc.body.dataset.loginShortcutBound = "1";

            // 默认聚焦账号框
            setTimeout(() => {
                const inputs = getVisibleLoginInputs();
                if (inputs.length >= 1) {
                    try {
                        inputs[0].focus();
                    } catch (e) {}
                }
            }, 500);

            doc.addEventListener("keydown", function(e) {
                const inputs = getVisibleLoginInputs();
                if (inputs.length < 2) {
                    return;
                }

                const usernameInput = inputs[0];
                const passwordInput = inputs[1];
                const active = doc.activeElement;

                if (e.key === "ArrowDown") {
                    if (active === usernameInput) {
                        e.preventDefault();
                        passwordInput.focus();
                    }
                }

                if (e.key === "ArrowUp") {
                    if (active === passwordInput) {
                        e.preventDefault();
                        usernameInput.focus();
                    }
                }

                // Enter 由 st.form_submit_button 原生提交，不在 JS 里强行点击
            });
        }

        setupLoginShortcuts();
        </script>
        """,
        height=0,
    )

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
        inject_login_keyboard_shortcuts()

        with st.form("login_form", clear_on_submit=False):
            u = st.text_input("账号", key="login_username")
            p = st.text_input("密码", type="password", key="login_password")
            submitted = st.form_submit_button("登录", type="primary", use_container_width=True)

        if submitted:
            role = login_user(u, p)
            if role:
                st.session_state.update({'logged_in': True, 'username': u, 'role': role})
                st.rerun()
            else:
                st.error("账号或密码错误")

    with t2:
        nu = st.text_input("新账号", key="register_username")
        np = st.text_input("新密码", type="password", key="register_password")
        nr = st.selectbox("角色", ["sales", "warehouse"], key="register_role")
        ic = st.text_input("邀请码", key="register_invitation_code")

        if st.button("注册", use_container_width=True, key="register_button"):
            if ic != INVITATION_CODE:
                st.error("邀请码错误")
            elif register_user(nu, np, nr):
                st.success("注册成功，请登录")
                st.rerun()
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
    
    # 隐藏以下暂不用页面：
    # 其他物料下单、入库、制衣厂消耗、跨境物料
    # 仅隐藏入口，不删除原模块代码，后续需要可恢复。
    if role == 'admin':
        tabs = st.tabs([
            "📊 看板",
            "🛒 采购合同下单",
            "📋 包装袋下单",
            "🖨️ 辅料下单",
            "📥 飞书采购申请",
            "📜 历史",
            "⚙️ 后台"
        ])

        with tabs[0]:
            render_dashboard()
        with tabs[1]:
            render_purchase_order(name)
        with tabs[2]:
            render_outbound(name)
        with tabs[3]:
            render_accessory_order(name)
        with tabs[4]:
            show_feishu_sync(name)
        with tabs[5]:
            render_history(role, name)
        with tabs[6]:
            render_admin()

    elif role == 'sales':
        tabs = st.tabs([
            "📊 看板",
            "🛒 采购合同下单",
            "📋 包装袋下单",
            "🖨️ 辅料下单",
            "📥 飞书采购申请",
            "📜 历史"
        ])

        with tabs[0]:
            render_dashboard()
        with tabs[1]:
            render_purchase_order(name)
        with tabs[2]:
            render_outbound(name)
        with tabs[3]:
            render_accessory_order(name)
        with tabs[4]:
            show_feishu_sync(name)
        with tabs[5]:
            render_history(role, name)

    else:
        tabs = st.tabs([
            "📊 看板",
            "📜 历史"
        ])

        with tabs[0]:
            render_dashboard()
        with tabs[1]:
            render_history(role, name)

if st.session_state['logged_in']:
    main()
else:
    login_page()