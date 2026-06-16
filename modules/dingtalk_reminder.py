#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import json
import datetime
import time
import hmac
import hashlib
import base64
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_db_conn

# ================== 飞书机器人配置 ==================
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/7a87b780-2b34-4842-9e1c-5ca2779dd6db"  # 请替换
FEISHU_SECRET = "6YKb45LxQ3fFX7houA11cf"   # 您的正确签名密钥

def send_feishu_message(text):
    """发送飞书文本消息（支持签名校验，严格按照官方文档）"""
    timestamp = str(int(time.time()))   # 秒级时间戳字符串
    # 签名计算：key = timestamp + "\n" + secret，对空字节进行 HMAC-SHA256
    secret = FEISHU_SECRET
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode('utf-8'),
        b'',   # 空字节内容
        hashlib.sha256
    ).digest()
    sign = base64.b64encode(hmac_code).decode('utf-8')
    
    # 请求体必须包含 timestamp 和 sign 字段
    payload = {
        "timestamp": timestamp,
        "sign": sign,
        "msg_type": "text",
        "content": {"text": text}
    }
    headers = {"Content-Type": "application/json"}
    
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, headers=headers, timeout=5)
        result = resp.json()
        if resp.status_code == 200 and result.get("code") == 0:
            print(f"飞书发送成功: {result}")
            return True
        else:
            print(f"飞书发送失败: {result}")
            return False
    except Exception as e:
        print(f"请求异常: {e}")
        return False

def extract_delivery_date(remark_json_str):
    # ... (与之前相同，保持不变)
    if not remark_json_str:
        return None
    try:
        items = json.loads(remark_json_str)
        if isinstance(items, list) and len(items) > 0:
            delivery = items[0].get("货期")
        elif isinstance(items, dict) and "items" in items:
            sub_items = items.get("items", [])
            if sub_items:
                delivery = sub_items[0].get("货期")
            else:
                return None
        else:
            return None
        if not delivery:
            return None
        if isinstance(delivery, str):
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
                try:
                    return datetime.datetime.strptime(delivery, fmt).date()
                except:
                    continue
            return None
        elif isinstance(delivery, datetime.date):
            return delivery
        else:
            return None
    except:
        return None

def check_and_remind():
    print("开始检查采购合同货期...")
    conn = get_db_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT reminded FROM purchase_orders LIMIT 1")
    except:
        print("错误：缺少 reminded 列，请执行：ALTER TABLE purchase_orders ADD COLUMN reminded TINYINT(1) DEFAULT 0;")
        return

    cursor.execute("""
        SELECT id, contract_no, create_time, factory_name, operator, remark
        FROM purchase_orders
        WHERE status = 'pending' AND reminded = 0
    """)
    rows = cursor.fetchall()
    today = datetime.date.today()
    reminded_count = 0
    for row in rows:
        po_id, contract_no, create_time, factory_name, operator, remark = row
        delivery_date = extract_delivery_date(remark)
        if not delivery_date:
            continue
        days_left = (delivery_date - today).days
        if 0 <= days_left <= 4:
            msg = f"""**【采购合同货期提醒】**
合同编号：{contract_no}
乙方工厂：{factory_name}
操作人：{operator}
货期：{delivery_date.strftime('%Y-%m-%d')}
剩余天数：{days_left} 天
请及时跟进！"""
            if send_feishu_message(msg):
                cursor.execute("UPDATE purchase_orders SET reminded = 0 WHERE id = %s", (po_id,))
                conn.commit()
                reminded_count += 1
                print(f"已提醒合同 {contract_no}")
            else:
                print(f"合同 {contract_no} 提醒发送失败")
    cursor.close()
    conn.close()
    print(f"检查完成，共发送 {reminded_count} 条提醒")

if __name__ == "__main__":
    check_and_remind()