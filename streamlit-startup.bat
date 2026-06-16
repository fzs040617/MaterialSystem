@echo off
chcp 65001 >nul

"C:\Users\admin\Desktop\MaterialSystem\venv\Scripts\python.exe" "C:\Users\admin\Desktop\MaterialSystem\streamlit-startup.py"
"C:\Users\admin\Desktop\MaterialSystem\venv\Scripts\python.exe" "C:\Users\admin\Desktop\MaterialSystem\modules\dingtalk_reminder.py"