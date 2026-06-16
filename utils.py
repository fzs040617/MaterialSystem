import hashlib
import re
import datetime
import subprocess
import sys
from openpyxl.utils import get_column_letter

# ==========================================
# 1. 财务与数字格式化工具
# ==========================================

def format_number_cn(num):
    """将数字格式化为中文万单位或标准数字字符串"""
    if num is None or num == 0: return "0"
    if num >= 10000:
        s = f"{num/10000:.2f}"
        if s.endswith(".00"): s = s[:-3]
        elif s.endswith("0"): s = s[:-1]
        return f"{s} 万"
    return f"{num}"

def convert_to_rmb_upper(n):
    """将阿拉伯数字金额转化为财务标准的中文大写金额"""
    fraction = ['角', '分']
    digit = ['零', '壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖']
    unit = [['元', '万', '亿'], ['', '拾', '佰', '仟']]
    
    n = abs(round(n, 2))
    if n == 0:
        return "零元整"
        
    s = ''
    for i in range(len(fraction)):
        f = int(round(n * 10 * (10 ** i), 2)) % 10
        if f != 0:
            s += digit[f] + fraction[i]
    if not s:
        s = '整'
        
    integer_part = int(n)
    if integer_part == 0:
        return "零元" + s
        
    integer_str = ''
    for i in range(len(unit[0])):
        if integer_part == 0:
            break
        p = ''
        for j in range(len(unit[1])):
            if integer_part == 0:
                break
            d = integer_part % 10
            if d != 0:
                p = digit[d] + unit[1][j] + p
            elif p and not p.startswith('零'):
                p = '零' + p
            integer_part //= 10
            
        if p:
            if p.endswith('零'):
                p = p[:-1]
            integer_str = p + unit[0][i] + integer_str
            
    return integer_str + s

# ==========================================
# 2. 安全与加密工具
# ==========================================

def make_hash(password):
    """对明文密码进行 SHA256 加密"""
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    """比对明文密码与加密后的哈希值是否匹配"""
    if make_hash(password) == hashed_text: return True
    return False

# ==========================================
# 3. 文件处理与环境工具
# ==========================================

def clean_filename(filename):
    """清洗文件名，剔除操作系统不支持的特殊字符"""
    return re.sub(r'[\\/*?:"<>|]', '_', filename)

def adjust_column_width(worksheet):
    """自动调整 OpenPyXL 工作表的列宽，使其适应内容长度"""
    for column in worksheet.columns:
        max_length = 0
        column_letter = get_column_letter(column[0].column)
        for cell in column:
            try:
                if len(str(cell.value)) > max_length: 
                    max_length = len(str(cell.value))
            except: pass
        # 根据字符长度计算建议列宽
        adjusted_width = (max_length + 4) * 1.5
        if adjusted_width > 50: adjusted_width = 50
        worksheet.column_dimensions[column_letter].width = adjusted_width

def auto_install_xlrd():
    """自动安装 xlrd 库以支持读取旧版 Excel 格式"""
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "xlrd>=2.0.1", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"])
        return True
    except: return False