# -*- coding: utf-8 -*-
import subprocess
import time
import sys
import socket
import re

# Set UTF-8 encoding
sys.stdout.reconfigure(encoding='utf-8')
subprocess.run(['chcp', '65001'], shell=True)

# Start Streamlit first and capture the port
# 把那行代码改成你现在的新路径：
streamlit_exe = r"C:\Users\admin\Desktop\MaterialSystem\venv\Scripts\streamlit.exe"
app_path = r"C:\Users\admin\Desktop\MaterialSystem\app.py"

# Start Streamlit and capture output
process = subprocess.Popen(
    [streamlit_exe, "run", app_path],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding='utf-8'
)

# Wait for Streamlit to start and get the port
port = 8501  # default
start_time = time.time()
while time.time() - start_time < 10:
    line = process.stdout.readline()
    if line:
        print(line.strip())
        match = re.search(r'Local URL:.*?:(\d+)', line)
        if match:
            port = match.group(1)
            break

# Send DingTalk message with the correct port
message = f"物料系统成功启动 - 地址 http://localhost:{port}"
cmd = f'openclaw message send --channel dingtalk --target 17636330729916655 --message "{message}"'
subprocess.run(cmd, shell=True, capture_output=True)
print(f"DingTalk notification sent: {message}")
