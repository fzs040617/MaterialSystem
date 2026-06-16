import json
import time
import requests
import math
import pandas as pd
from database import get_db_conn
from feishu_sync import get_tenant_access_token, get_real_bitable_token
from datetime import datetime

def safe_log(msg):
    try:
        print(msg.encode('utf-8', errors='ignore').decode('utf-8'))
    except Exception:
        pass

# 🌟 全局时间转换函数：所有迁移任务共用
def to_feishu_date(date_str):
    try:
        if not date_str or str(date_str).strip() == '': return None
        # 将 '2026-04-10' 转为 datetime 对象
        dt = datetime.strptime(str(date_str).strip()[:10], '%Y-%m-%d')
        # 转为 Unix 时间戳（秒），飞书 API 通常需要毫秒，所以乘以 1000
        return int(dt.timestamp() * 1000)
    except:
        return None

def fetch_feishu_all_records(app_token, table_id, headers):
    # ... (原有代码保持不变) ...
    """拉取飞书已有的记录，用于防重复写入"""
    all_items = []
    page_token = ""
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=500&page_token={page_token}"
        res = requests.get(url, headers=headers).json()
        if res.get("code") != 0:
            break
        data = res.get("data", {})
        all_items.extend(data.get("items", []))
        page_token = data.get("page_token", "")
        if not page_token: 
            break
    return all_items



def migrate_purchase_orders_to_feishu():
    """
    将本地 MySQL 的采购合同记录（含 JSON 嵌套）扁平化并单向推送到飞书多维表格。
    """
    # 这是你刚刚创建的飞书多维表格信息
    wiki_token = "UD5YweaYsi1AmkkD82Yc6wtBnic"
    table_id = "tblER5mLVkFgDycN" # 假设这是【采购合同明细表】的 ID
    
    safe_log("[1] 正在获取飞书云端全量访问凭证...")
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    if not app_token or not access_token:
        safe_log("❌ 获取飞书 Token 失败。")
        return

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    # 1. 先拉取飞书现有的记录，防止每次运行脚本都重复插入
    safe_log("[2] 正在拉取飞书现有历史记录（用于防重验证）...")
    existing_records = fetch_feishu_all_records(app_token, table_id, headers)
    
    # 构建飞书防重集合，使用 "系统单号_物料编号" 作为唯一身份标识
    existing_keys = set()
    for item in existing_records:
        fields = item.get('fields', {})
        sys_no = str(fields.get('系统单号', '')).strip()
        mat_no = str(fields.get('物料编号', '')).strip()
        if sys_no:
            existing_keys.add(f"{sys_no}_{mat_no}")

    # 2. 从本地 MySQL 读取采购合同数据
    safe_log("[3] 正在从 MySQL 读取【采购合同历史】...")
    conn = get_db_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, contract_no, create_time, factory_name, operator, remark FROM purchase_orders ORDER BY id ASC")
        rows = cursor.fetchall()
    except Exception as e:
        safe_log(f"❌ 读取 MySQL 失败: {e}")
        return
    finally:
        cursor.close()
        conn.close()

    if not rows:
        safe_log("⚠️ 本地没有采购合同记录。")
        return

    # 3. 将 JSON 嵌套数据“扁平化（拆行）”
    payloads_to_insert = []
    
    for row in rows:
        sys_id = row[0]
        contract_no = str(row[1] or "")
        create_time = str(row[2] or "")
        factory_name = str(row[3] or "")
        operator = str(row[4] or "")
        remark_json = row[5]
        
        system_order_no = f"PO-{sys_id:04d}"
        
        try:
            raw_data = json.loads(remark_json) if remark_json else []
            items = raw_data if isinstance(raw_data, list) else raw_data.get('items', [])
        except:
            items = []
            
        for item in items:
            mat_no = str(item.get('物料编号', '')).strip()
            
            # 防重校验：如果飞书里已经有这行物料，跳过
            unique_key = f"{system_order_no}_{mat_no}"
            if unique_key in existing_keys:
                continue
                
            # 🌟 核心修复：彻底拦截 None, 空字符串, 以及致命的 NaN 和 Infinity
            def safe_float(val):
                try:
                    if val is None or str(val).strip() == '':
                        return 0.0
                    f_val = float(val)
                    # 如果转换出来是 nan (非数字) 或 inf (无穷大)，强制设为 0.0
                    if math.isnan(f_val) or math.isinf(f_val):
                        return 0.0
                    return f_val
                except Exception:
                    return 0.0

            qty = safe_float(item.get('数量', 0))
            price = safe_float(item.get('单价', 0.0))
            
            # 🌟 核心修复：将日期字符串转为飞书要求的 Unix 毫秒时间戳
            def to_feishu_date(date_str):
                try:
                    if not date_str or date_str.strip() == '': return None
                    # 将 '2026-04-10' 转为 datetime 对象
                    dt = datetime.strptime(str(date_str).strip(), '%Y-%m-%d')
                    # 转为 Unix 时间戳（秒），飞书 API 通常需要毫秒，所以乘以 1000
                    return int(dt.timestamp() * 1000)
                except:
                    return None # 如果格式乱七八糟，直接传 None 忽略掉
            
            # 构建符合飞书表格字段要求的数据字典
            field_data = {
                "系统单号": system_order_no,
                "合同编号": contract_no,
                "乙方工厂": factory_name,
                "物料编号": mat_no,
                "物料名称": str(item.get('物料名称', '')),
                "材质": str(item.get('材质', '')),
                "尺寸": str(item.get('尺寸', '')),
                "颜色": str(item.get('颜色', '')),
                "收货标准": str(item.get('收货标准', '')),
                "数量": qty,
                "单位": str(item.get('单位', 'Pcs')),
                "单价": price,
                "总金额": round(qty * price, 3), # 本地算好总金额推过去
                "货期": to_feishu_date(item.get('货期', '')),
                "操作人": operator,
                "备注": str(item.get('备注', ''))
            }
            
            # 只有有值的字段才传，防止飞书报错
            clean_field_data = {k: v for k, v in field_data.items() if v != "" and v is not None}
            payloads_to_insert.append({"fields": clean_field_data})

    # 4. 执行批量推送（飞书 API 支持每次批量写入最多 500 条）
    if not payloads_to_insert:
        safe_log("💡 没有发现新的历史记录，飞书端已是最新。")
        return
        
    safe_log(f"[4] 准备向飞书写入 {len(payloads_to_insert)} 条明细记录...")
    
    # 切片，每批 100 条，防止单次请求过大
    batch_size = 50
    insert_count = 0
    fail_count = 0
    
    for i in range(0, len(payloads_to_insert), batch_size):
        batch = payloads_to_insert[i : i + batch_size]
        url_create = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
        
        try:
            # 找到循环里的 requests.post，加上 timeout 参数
            res = requests.post(url_create, headers=headers, json={"records": batch}, timeout=30).json()
            if res.get("code") == 0:
                insert_count += len(batch)
            else:
                # 👈 核心：把飞书完整的报错信息打印出来
                safe_log(f"❌ 批量写入失败详情: {res}") 
                fail_count += len(batch)
        except Exception as e:
            safe_log(f"❌ 网络请求异常: {e}")
            fail_count += len(batch)
            
        time.sleep(0.2) # API 防频控

    safe_log(f"🎉 历史记录迁移完成！成功写入 {insert_count} 条，失败 {fail_count} 条。")

def delete_feishu_purchase_order(po_id):
    """
    根据本地的 po_id (例如 1)，去飞书把对应的行全部删掉。
    采购合同的系统单号格式为: PO-0001
    """
    import requests
    from feishu_sync import get_tenant_access_token, get_real_bitable_token
    
    wiki_token = "UD5YweaYsi1AmkkD82Yc6wtBnic"
    table_id = "tblER5mLVkFgDycN"  # 采购合同表 ID
    
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    if not app_token or not access_token:
        return

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}
    
    # 将本地 ID 转换为飞书里的 系统单号，例如: PO-0001
    target_sys_no = f"PO-{int(po_id):04d}"
    
    # 1. 拉取飞书现有记录，寻找匹配的内部 record_id
    existing_records = fetch_feishu_all_records(app_token, table_id, headers)
    
    records_to_delete = []
    for item in existing_records:
        fields = item.get('fields', {})
        sys_no = str(fields.get('系统单号', '')).strip()
        
        # 如果系统单号匹配，提取飞书自带的底层 record_id
        if sys_no == target_sys_no:
            # 注意：内部 ID 是存在字典外层的 'record_id' 字段里
            records_to_delete.append(item['record_id'])
            
    if not records_to_delete:
        return  # 飞书里没有对应记录，不需要删
        
    # 2. 调用飞书批量删除 API
    url_delete = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
    payload = {"records": records_to_delete}
    
    try:
        requests.post(url_delete, headers=headers, json=payload, timeout=15)
    except Exception as e:
        safe_log(f"飞书同步删除失败: {str(e)}")


def migrate_order_history_to_feishu():
    """迁移包装袋发货明细 (order_history)"""
    wiki_token = "UD5YweaYsi1AmkkD82Yc6wtBnic"
    table_id = "tblC9x54B4aEJeGa"  # ⚠️ 请确保这是你为“包装袋发货明细”建立的表 ID
    
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}

    # 1. 获取现有记录，构建防重 Key (使用最新校准的飞书真实列名)
    existing_records = fetch_feishu_all_records(app_token, table_id, headers)
    existing_keys = set()
    for item in existing_records:
        fields = item.get('fields', {})
        rec_id = str(fields.get('记录ID', '')).strip()
        garment = str(fields.get('接收制衣厂', '')).strip()
        qty = str(fields.get('路线发货数量', '0')).strip()
        
        if rec_id:
            # 防重标识：单号_制衣厂_数量 (解决同一个单号发给多个厂的拆行重复问题)
            existing_keys.add(f"{rec_id}_{garment}_{qty}")

    # 2. 读取 MySQL (使用 SQLAlchemy 引擎，消除警告，更稳定)
    from database import get_db_engine
    engine = get_db_engine()
    df = pd.read_sql_query("SELECT * FROM order_history ORDER BY id ASC", engine)
    
    # ==========================================
    # 🌟 新增：读取本地最新库存数据，构建三维价格映射表
    # ==========================================
    inv_df = pd.read_sql_query("SELECT factory_name, bag_name, bag_size, unit_price FROM inventory", engine)
    price_map = {}
    if not inv_df.empty:
        for _, inv_row in inv_df.iterrows():
            fac = str(inv_row.get('factory_name', '')).strip()
            b_n = str(inv_row.get('bag_name', '')).strip()
            b_s = str(inv_row.get('bag_size', '')).strip()
            try:
                price_map[f"{fac}_{b_n}_{b_s}"] = float(inv_row.get('unit_price', 0.0))
            except:
                price_map[f"{fac}_{b_n}_{b_s}"] = 0.0
    # ==========================================
    
    engine.dispose() # 释放连接池

    payloads = []
    for _, row in df.iterrows():
        try:
            details = json.loads(row['details'])
            shipping_list = details.get('shipping', [])
            
            for item in shipping_list:
                src_fac = item.get('src_factory', '')
                dst_gar = item.get('dst_garment', '')
                qty = item.get('qty', 0)
                
                unique_key = f"BAG-{row['id']}_{dst_gar}_{qty}"
                if unique_key in existing_keys:
                    continue
                
                # ==========================================
                # 🌟 新增：提取三维特征进行价格匹配，并计算小计
                # ==========================================
                bag_n = str(row.get('bag_name', '')).strip()
                bag_s = str(row.get('bag_size', '')).strip()
                fac_str = str(src_fac).strip()
                
                bag_key = f"{fac_str}_{bag_n}_{bag_s}"
                u_price = price_map.get(bag_key, 0.0)
                total_price = u_price * int(qty)
                
                # 严格对齐飞书字段
                field_data = {
                    "记录ID": f"BAG-{row['id']}",
                    "下单日期": to_feishu_date(row['order_date']), 
                    "销售平台": row['platform'],
                    "商品名称": row['product_name'],
                    "包装袋名称": row['bag_name'],
                    "尺寸": row['bag_size'],
                    "发货源工厂": src_fac,
                    "接收制衣厂": dst_gar,
                    "路线发货数量": int(qty),
                    "单价": float(u_price),      # 👈 新增写入
                    "小计": float(total_price),  # 👈 新增写入
                    "操作人": row['operator']
                }
                
                clean_field_data = {k: v for k, v in field_data.items() if v != "" and v is not None}
                payloads.append({"fields": clean_field_data})
        except:
            continue

    # 3. 批量推送 (强化监控版)
    if payloads:
        safe_log(f"✅ 数据准备完毕，共需写入 {len(payloads)} 条明细。")
        batch_size = 50  # 强制降频，每次只传 50 条
        insert_count = 0
        fail_count = 0
        
        for i in range(0, len(payloads), batch_size):
            batch = payloads[i : i + batch_size]
            url_create = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
            
            safe_log(f"  👉 正在发送第 {i+1} 到 {i+len(batch)} 条...")
            
            try:
                # 检查序列化是否会崩溃 (如果数据有毒，这里会立刻报错)
                json_data = {"records": batch}
                _ = json.dumps(json_data) 
                
                # 发送请求，强制加上 15 秒超时
                res_obj = requests.post(url_create, headers=headers, json=json_data, timeout=15)
                res = res_obj.json()
                
                if res.get("code") == 0:
                    insert_count += len(batch)
                    safe_log(f"    ✅ 成功写入！")
                else:
                    safe_log(f"    ❌ 飞书拒绝接收: {res}")
                    fail_count += len(batch)
                    
            except requests.exceptions.Timeout:
                safe_log(f"    ❌ 网络超时！飞书服务器没有响应。")
                fail_count += len(batch)
            except Exception as e:
                safe_log(f"    ❌ 发生严重异常: {str(e)}")
                fail_count += len(batch)
                
            time.sleep(0.5) # 必须有休眠，防止触发飞书 QPS 限制被强杀
            
        safe_log(f"🎉 包装袋明细迁移结束。成功: {insert_count}，失败: {fail_count}。")
    else:
        safe_log("💡 没有发现新的历史记录，飞书端已是最新。")

def delete_feishu_bag_order(order_id):
    """撤销包装袋发货记录：根据 BAG-ID 查找并删除"""
    wiki_token = "UD5YweaYsi1AmkkD82Yc6wtBnic"
    table_id = "tblC9x54B4aEJeGa" # 👈 确保 ID 正确
    
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}
    
    target_id = f"BAG-{int(order_id)}"
    records = fetch_feishu_all_records(app_token, table_id, headers)
    
    delete_list = [r['record_id'] for r in records if str(r['fields'].get('记录ID', '')).strip() == target_id]
    
    if delete_list:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
        requests.post(url, headers=headers, json={"records": delete_list}, timeout=15)


def migrate_other_material_history_to_feishu():
    """迁移其他物料发货明细 (other_material_history)"""
    wiki_token = "UD5YweaYsi1AmkkD82Yc6wtBnic"
    table_id = "tblXWkYr8fhvxuGc"  # ⚠️ 请务必替换为飞书里【其他物料发货明细表】的真实 ID
    
    safe_log("\n[其他物料发货] 正在获取飞书云端凭证...")
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}

    # 1. 获取现有记录，构建防重 Key (使用最新校准的飞书真实列名)
    existing_records = fetch_feishu_all_records(app_token, table_id, headers)
    existing_keys = set()
    for item in existing_records:
        fields = item.get('fields', {})
        rec_id = str(fields.get('记录ID', '')).strip()
        garment = str(fields.get('接收制衣厂', '')).strip()
        qty = str(fields.get('路线发货数量', '0')).strip()
        
        if rec_id:
            existing_keys.add(f"{rec_id}_{garment}_{qty}")

    # 2. 读取 MySQL (使用 SQLAlchemy 引擎)
    from database import get_db_engine
    engine = get_db_engine()
    df = pd.read_sql_query("SELECT * FROM other_material_history ORDER BY id ASC", engine)
    engine.dispose() 

    payloads = []
    for _, row in df.iterrows():
        try:
            details = json.loads(row['details'])
            
            # 🌟 核心修复：强制转换为字符串，防范 JSON 里潜伏的数字类型
            raw_src = details.get('src_factory', '')
            raw_dst = details.get('dst_garment', '')
            qty = details.get('qty', 0)
            
            src_fac = str(raw_src).strip() if raw_src is not None and raw_src != "" else ""
            dst_gar = str(raw_dst).strip() if raw_dst is not None and raw_dst != "" else ""
            
            unique_key = f"MAT-{row['id']}_{dst_gar}_{qty}"
            if unique_key in existing_keys:
                continue
            
            # 严格对齐飞书字段
            field_data = {
                "记录ID": f"MAT-{row['id']}",
                "下单日期": to_feishu_date(row['order_date']),
                "物料名称": str(row['material_display']).strip(),
                "发货源工厂": src_fac,
                "接收制衣厂": dst_gar,
                "路线发货数量": int(float(qty)) if pd.notna(qty) else 0,
                "操作人": str(row['operator']).strip() if row['operator'] else ""
            }
            
            clean_field_data = {k: v for k, v in field_data.items() if v != "" and v is not None}
            payloads.append({"fields": clean_field_data})
        except Exception as e:
            continue

    # 3. 批量推送 (强化监控版)
    if payloads:
        safe_log(f"✅ 数据准备完毕，共需写入 {len(payloads)} 条其他物料明细。")
        batch_size = 50 
        insert_count = 0
        fail_count = 0
        
        for i in range(0, len(payloads), batch_size):
            batch = payloads[i : i + batch_size]
            url_create = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
            
            safe_log(f"  👉 正在发送第 {i+1} 到 {i+len(batch)} 条...")
            
            try:
                json_data = {"records": batch}
                _ = json.dumps(json_data) 
                
                res_obj = requests.post(url_create, headers=headers, json=json_data, timeout=15)
                res = res_obj.json()
                
                if res.get("code") == 0:
                    insert_count += len(batch)
                    safe_log(f"    ✅ 成功写入！")
                else:
                    safe_log(f"    ❌ 飞书拒绝接收: {res}")
                    fail_count += len(batch)
                    
            except requests.exceptions.Timeout:
                safe_log(f"    ❌ 网络超时！飞书服务器没有响应。")
                fail_count += len(batch)
            except Exception as e:
                safe_log(f"    ❌ 发生严重异常: {str(e)}")
                fail_count += len(batch)
                
            time.sleep(0.5)
            
        safe_log(f"🎉 其他物料发货明细迁移结束。成功: {insert_count}，失败: {fail_count}。")
    else:
        safe_log("💡 其他物料发货明细：飞书端已是最新。")

def delete_feishu_other_material(mat_id):
    """撤销其他物料记录：根据 MAT-ID 查找并删除"""
    wiki_token = "UD5YweaYsi1AmkkD82Yc6wtBnic"
    table_id = "tblXWkYr8fhvxuGc" # 👈 确保 ID 正确
    
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}
    
    target_id = f"MAT-{int(mat_id)}"
    records = fetch_feishu_all_records(app_token, table_id, headers)
    
    delete_list = [r['record_id'] for r in records if str(r['fields'].get('记录ID', '')).strip() == target_id]
    
    if delete_list:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
        requests.post(url, headers=headers, json={"records": delete_list}, timeout=15)


def migrate_accessory_history_to_feishu():
    """迁移辅料下单记录 (accessory_history)"""
    wiki_token = "UD5YweaYsi1AmkkD82Yc6wtBnic"
    table_id = "tblZybjriiIyJSTo"  # ⚠️ 请务必替换为飞书里【辅料下单记录表】的真实 ID
    
    safe_log("\n[辅料下单记录] 正在获取飞书云端凭证...")
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}

    # 1. 获取现有记录，构建防重 Key
    existing_records = fetch_feishu_all_records(app_token, table_id, headers)
    existing_keys = set()
    for item in existing_records:
        fields = item.get('fields', {})
        rec_id = str(fields.get('记录ID', '')).strip()
        if rec_id:
            existing_keys.add(rec_id)

    # 2. 读取 MySQL
    from database import get_db_engine
    engine = get_db_engine()
    df = pd.read_sql_query("SELECT * FROM accessory_history ORDER BY id ASC", engine)
    engine.dispose() 
    
    df = df.where(pd.notna(df), None)

    payloads = []
    for _, row in df.iterrows():
        try:
            unique_key = f"ACC-{row['id']}"
            if unique_key in existing_keys:
                continue
                
            def safe_int(val):
                try: return int(float(val))
                except: return 0

            field_data = {
                "记录ID": unique_key,
                "下单日期": to_feishu_date(row['order_date']),
                "货号": row['item_no'],
                "产品名称": row['product_name'],
                "辅料款式": row['acc_style'],
                "内部码": row['internal_code'],
                "成分资料": row['material_info'],
                "收货制衣厂": row['factory_name'],
                "下单数量": safe_int(row['total_qty']),
                "操作人": row['operator']
            }
            
            # 🌟 终极防 nan 过滤网：不仅过滤空值，彻底绞杀隐藏的 NaN
            clean_field_data = {}
            for k, v in field_data.items():
                if v == "" or v is None: 
                    continue
                if isinstance(v, float) and math.isnan(v): 
                    continue
                clean_field_data[k] = v
                
            payloads.append({"fields": clean_field_data})
        except Exception as e:
            continue

    # 3. 批量推送 (包含正确位置的排错打印)
    if payloads:
        safe_log(f"✅ 数据准备完毕，共需写入 {len(payloads)} 条辅料记录。")
        batch_size = 50 
        insert_count = 0
        fail_count = 0
        
        for i in range(0, len(payloads), batch_size):
            batch = payloads[i : i + batch_size]
            url_create = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
            
            safe_log(f"  👉 正在发送第 {i+1} 到 {i+len(batch)} 条...")
            
            try:
                json_data = {"records": batch}
                _ = json.dumps(json_data) 
                
                res_obj = requests.post(url_create, headers=headers, json=json_data, timeout=15)
                res = res_obj.json()
                
                if res.get("code") == 0:
                    insert_count += len(batch)
                    safe_log(f"    ✅ 成功写入！")
                else:
                    safe_log(f"    ❌ 飞书拒绝接收: {res}")
                    fail_count += len(batch)
                    
            except requests.exceptions.Timeout:
                safe_log(f"    ❌ 网络超时！飞书服务器没有响应。")
                fail_count += len(batch)
            except Exception as e:
                safe_log(f"    ❌ 发生严重异常: {str(e)}")
                # 👇 这里才是正确触发打印的位置！
                safe_log("    ⚠️ 导致崩溃的具体数据如下：")
                for bad_item in batch:
                    safe_log(f"      {bad_item}")
                fail_count += len(batch)
                
            time.sleep(0.5)
            
        safe_log(f"🎉 辅料下单记录迁移结束。成功: {insert_count}，失败: {fail_count}。")
    else:
        safe_log("💡 辅料下单记录：飞书端已是最新。")

def delete_feishu_accessory_order(record_id):
    """
    根据本地的 accessory_history.id 删除飞书多维表格中的对应行
    飞书表格中的“记录ID”字段格式为 ACC-{id}
    """
    wiki_token = "UD5YweaYsi1AmkkD82Yc6wtBnic"
    table_id = "tblZybjriiIyJSTo"          # 辅料下单记录表的真实 ID
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    if not app_token or not access_token:
        return

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}
    target_id = f"ACC-{int(record_id)}"

    # 1. 拉取飞书现有记录，找出匹配的 record_id
    existing_records = fetch_feishu_all_records(app_token, table_id, headers)
    delete_list = []
    for item in existing_records:
        fields = item.get('fields', {})
        rec_id = str(fields.get('记录ID', '')).strip()
        if rec_id == target_id:
            delete_list.append(item['record_id'])

    # 2. 调用批量删除接口
    if delete_list:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
        try:
            requests.post(url, headers=headers, json={"records": delete_list}, timeout=15)
        except Exception as e:
            safe_log(f"飞书辅料记录删除失败: {e}")

def migrate_inbound_history_to_feishu():
    """迁移包装袋入库记录表 (inbound_history)"""
    wiki_token = "UD5YweaYsi1AmkkD82Yc6wtBnic"
    table_id = "tbl6LokKtl6AKNBq"  # 👈 你提供的真实入库表 ID
    
    safe_log("\n[入库记录] 正在获取飞书云端凭证...")
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}

    # 1. 获取现有记录，构建防重 Key
    existing_records = fetch_feishu_all_records(app_token, table_id, headers)
    existing_keys = set()
    for item in existing_records:
        fields = item.get('fields', {})
        rec_id = str(fields.get('记录ID', '')).strip()
        if rec_id:
            existing_keys.add(rec_id)

    # 2. 读取 MySQL
    from database import get_db_engine
    engine = get_db_engine()
    df = pd.read_sql_query("SELECT * FROM inbound_history ORDER BY id ASC", engine)
    engine.dispose() 
    
    # 🌟 核心防空值机制
    df = df.where(pd.notna(df), None)

    payloads = []
    for _, row in df.iterrows():
        try:
            unique_key = f"INB-{row['id']}"
            if unique_key in existing_keys:
                continue
                
            def safe_int(val):
                try: return int(float(val))
                except: return 0

            # 🌟 严格对齐您的 8 个标准字段（带括号的字段名已精确还原）
            field_data = {
                "记录ID": unique_key,
                "入库日期": to_feishu_date(row['inbound_date']),
                "发货工厂(来源)": row['factory_name'],
                "包装袋名称": row['bag_name'],
                "尺寸": row['bag_size'],
                "入库数量": safe_int(row['quantity']),
                "备注": row['note'],
                "操作人": row['operator']
            }
            
            # 🌟 终极防 nan 过滤网：不仅过滤空值，彻底绞杀隐藏的 NaN
            clean_field_data = {}
            for k, v in field_data.items():
                if v == "" or v is None: 
                    continue
                if isinstance(v, float) and math.isnan(v): 
                    continue
                clean_field_data[k] = v
                
            payloads.append({"fields": clean_field_data})
        except Exception as e:
            continue

    # 3. 批量推送 (包含正确位置的排错打印)
    if payloads:
        safe_log(f"✅ 数据准备完毕，共需写入 {len(payloads)} 条入库记录。")
        batch_size = 50 
        insert_count = 0
        fail_count = 0
        
        for i in range(0, len(payloads), batch_size):
            batch = payloads[i : i + batch_size]
            url_create = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
            
            safe_log(f"  👉 正在发送第 {i+1} 到 {i+len(batch)} 条...")
            
            try:
                json_data = {"records": batch}
                _ = json.dumps(json_data) 
                
                res_obj = requests.post(url_create, headers=headers, json=json_data, timeout=15)
                res = res_obj.json()
                
                if res.get("code") == 0:
                    insert_count += len(batch)
                    safe_log(f"    ✅ 成功写入！")
                else:
                    safe_log(f"    ❌ 飞书拒绝接收: {res}")
                    fail_count += len(batch)
                    
            except requests.exceptions.Timeout:
                safe_log(f"    ❌ 网络超时！飞书服务器没有响应。")
                fail_count += len(batch)
            except Exception as e:
                safe_log(f"    ❌ 发生严重异常: {str(e)}")
                safe_log("    ⚠️ 导致崩溃的具体数据如下：")
                for bad_item in batch:
                    safe_log(f"      {bad_item}")
                fail_count += len(batch)
                
            time.sleep(0.5)
            
        safe_log(f"🎉 入库记录迁移结束。成功: {insert_count}，失败: {fail_count}。")
    else:
        safe_log("💡 入库记录：飞书端已是最新。")

def delete_feishu_inbound_order(record_id):
    """
    根据本地 inbound_history.id 删除飞书多维表格中的对应行
    飞书表格中的“记录ID”字段格式为 INB-{id}
    """
    wiki_token = "UD5YweaYsi1AmkkD82Yc6wtBnic"
    table_id = "tbl6LokKtl6AKNBq"          # 入库记录表的真实 ID
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    if not app_token or not access_token:
        return

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}
    target_id = f"INB-{int(record_id)}"

    existing_records = fetch_feishu_all_records(app_token, table_id, headers)
    delete_list = []
    for item in existing_records:
        fields = item.get('fields', {})
        rec_id = str(fields.get('记录ID', '')).strip()
        if rec_id == target_id:
            delete_list.append(item['record_id'])

    if delete_list:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
        try:
            requests.post(url, headers=headers, json={"records": delete_list}, timeout=15)
        except Exception as e:
            safe_log(f"飞书入库记录删除失败: {e}")

def migrate_garment_consumption_to_feishu():
    """迁移制衣厂消耗记录表 (garment_consumption)"""
    wiki_token = "UD5YweaYsi1AmkkD82Yc6wtBnic"
    table_id = "tbljpZJMUa0DOpWZ"  # 👈 你提供的真实消耗记录表 ID
    
    safe_log("\n[消耗记录] 正在获取飞书云端凭证...")
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}

    # 1. 获取现有记录，构建防重 Key
    existing_records = fetch_feishu_all_records(app_token, table_id, headers)
    existing_keys = set()
    for item in existing_records:
        fields = item.get('fields', {})
        rec_id = str(fields.get('记录ID', '')).strip()
        if rec_id:
            existing_keys.add(rec_id)

    # 2. 读取 MySQL
    from database import get_db_engine
    engine = get_db_engine()
    df = pd.read_sql_query("SELECT * FROM garment_consumption ORDER BY id ASC", engine)
    engine.dispose() 
    
    # 🌟 核心防空值机制
    df = df.where(pd.notna(df), None)

    payloads = []
    for _, row in df.iterrows():
        try:
            unique_key = f"CON-{row['id']}"
            if unique_key in existing_keys:
                continue
                
            def safe_int(val):
                try: return int(float(val))
                except: return 0

            # 🌟 严格对齐您的 8 个标准字段
            field_data = {
                "记录ID": unique_key,
                "消耗日期": to_feishu_date(row['consume_date']),
                "制衣厂名称": row['factory_name'],
                "关联生产订单号/款号": row['order_no'],
                "包装袋名称": row['bag_name'],
                "尺寸": row['bag_size'],
                "消耗数量": safe_int(row['quantity']),
                "操作人": row['operator']
            }
            
            # 🌟 终极防 nan 过滤网：不仅过滤空值，彻底绞杀隐藏的 NaN
            clean_field_data = {}
            for k, v in field_data.items():
                if v == "" or v is None: 
                    continue
                if isinstance(v, float) and math.isnan(v): 
                    continue
                clean_field_data[k] = v
                
            payloads.append({"fields": clean_field_data})
        except Exception as e:
            continue

    # 3. 批量推送 (包含正确位置的排错打印)
    if payloads:
        safe_log(f"✅ 数据准备完毕，共需写入 {len(payloads)} 条消耗记录。")
        batch_size = 50 
        insert_count = 0
        fail_count = 0
        
        for i in range(0, len(payloads), batch_size):
            batch = payloads[i : i + batch_size]
            url_create = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
            
            safe_log(f"  👉 正在发送第 {i+1} 到 {i+len(batch)} 条...")
            
            try:
                json_data = {"records": batch}
                _ = json.dumps(json_data) 
                
                res_obj = requests.post(url_create, headers=headers, json=json_data, timeout=15)
                res = res_obj.json()
                
                if res.get("code") == 0:
                    insert_count += len(batch)
                    safe_log(f"    ✅ 成功写入！")
                else:
                    safe_log(f"    ❌ 飞书拒绝接收: {res}")
                    fail_count += len(batch)
                    
            except requests.exceptions.Timeout:
                safe_log(f"    ❌ 网络超时！飞书服务器没有响应。")
                fail_count += len(batch)
            except Exception as e:
                safe_log(f"    ❌ 发生严重异常: {str(e)}")
                safe_log("    ⚠️ 导致崩溃的具体数据如下：")
                for bad_item in batch:
                    safe_log(f"      {bad_item}")
                fail_count += len(batch)
                
            time.sleep(0.5)
            
        safe_log(f"🎉 消耗记录迁移结束。成功: {insert_count}，失败: {fail_count}。")
    else:
        safe_log("💡 消耗记录：飞书端已是最新。")

def delete_feishu_garment_consumption(record_id):
    """
    根据本地 garment_consumption.id 删除飞书多维表格中的对应行
    飞书表格中的“记录ID”字段格式为 CON-{id}
    """
    wiki_token = "UD5YweaYsi1AmkkD82Yc6wtBnic"
    table_id = "tbljpZJMUa0DOpWZ"          # 制衣厂消耗记录表的真实 ID
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    if not app_token or not access_token:
        return

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}
    target_id = f"CON-{int(record_id)}"

    existing_records = fetch_feishu_all_records(app_token, table_id, headers)
    delete_list = []
    for item in existing_records:
        fields = item.get('fields', {})
        rec_id = str(fields.get('记录ID', '')).strip()
        if rec_id == target_id:
            delete_list.append(item['record_id'])

    if delete_list:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
        try:
            requests.post(url, headers=headers, json={"records": delete_list}, timeout=15)
        except Exception as e:
            safe_log(f"飞书消耗记录删除失败: {e}")

def migrate_crossborder_orders_to_feishu():
    """迁移跨境物料下单记录表"""
    wiki_token = "UD5YweaYsi1AmkkD82Yc6wtBnic"
    table_id = "tblAxLSjtGdLkL9z"  # 👈 真实跨境物料表 ID
    
    safe_log("\n[跨境物料记录] 正在获取飞书云端凭证...")
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}

    # 1. 获取现有记录，构建防重 Key (字段名为 订单ID)
    existing_records = fetch_feishu_all_records(app_token, table_id, headers)
    existing_keys = set()
    for item in existing_records:
        fields = item.get('fields', {})
        rec_id = str(fields.get('订单ID', '')).strip()
        if rec_id:
            existing_keys.add(rec_id)

    # 2. 读取 MySQL (联表查询，严格对齐你的 history.py 逻辑)
    from database import get_db_engine
    engine = get_db_engine()
    df = pd.read_sql_query("""
        SELECT 
            o.id AS 订单ID,
            o.order_time AS 下单时间,
            m.name_model AS 物料名称,
            m.material_code AS 物料编码,
            o.quantity AS 数量,
            o.operator AS 操作人
        FROM crossborder_orders_v2 o
        JOIN crossborder_materials_v2 m ON o.material_id = m.id
        ORDER BY o.id ASC
    """, engine)
    engine.dispose() 
    
    # 🌟 核心防空值机制
    df = df.where(pd.notna(df), None)

    payloads = []
    for _, row in df.iterrows():
        try:
            unique_key = f"CB-{row['订单ID']}"
            if unique_key in existing_keys:
                continue
                
            def safe_int(val):
                try: return int(float(val))
                except: return 0

            # 🌟 严格对齐您的 6 个标准字段
            field_data = {
                "订单ID": unique_key,
                "下单时间": to_feishu_date(row['下单时间']),
                "物料编码": row['物料编码'],
                "物料名称及型号": row['物料名称'],
                "消耗数量": safe_int(row['数量']),
                "操作人": row['操作人']
            }
            
            # 🌟 终极防 nan 过滤网：彻底绞杀隐藏的 NaN
            clean_field_data = {}
            for k, v in field_data.items():
                if v == "" or v is None: 
                    continue
                if isinstance(v, float) and math.isnan(v): 
                    continue
                clean_field_data[k] = v
                
            payloads.append({"fields": clean_field_data})
        except Exception as e:
            continue

    # 3. 批量推送 (包含正确位置的排错打印)
    if payloads:
        safe_log(f"✅ 数据准备完毕，共需写入 {len(payloads)} 条跨境物料记录。")
        batch_size = 50 
        insert_count = 0
        fail_count = 0
        
        for i in range(0, len(payloads), batch_size):
            batch = payloads[i : i + batch_size]
            url_create = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
            
            safe_log(f"  👉 正在发送第 {i+1} 到 {i+len(batch)} 条...")
            
            try:
                json_data = {"records": batch}
                _ = json.dumps(json_data) 
                
                res_obj = requests.post(url_create, headers=headers, json=json_data, timeout=15)
                res = res_obj.json()
                
                if res.get("code") == 0:
                    insert_count += len(batch)
                    safe_log(f"    ✅ 成功写入！")
                else:
                    safe_log(f"    ❌ 飞书拒绝接收: {res}")
                    fail_count += len(batch)
                    
            except requests.exceptions.Timeout:
                safe_log(f"    ❌ 网络超时！飞书服务器没有响应。")
                fail_count += len(batch)
            except Exception as e:
                safe_log(f"    ❌ 发生严重异常: {str(e)}")
                safe_log("    ⚠️ 导致崩溃的具体数据如下：")
                for bad_item in batch:
                    safe_log(f"      {bad_item}")
                fail_count += len(batch)
                
            time.sleep(0.5)
            
        safe_log(f"🎉 跨境物料记录迁移结束。成功: {insert_count}，失败: {fail_count}。")
    else:
        safe_log("💡 跨境物料记录：飞书端已是最新。")

def delete_feishu_crossborder_order(order_id):
    """
    根据本地 crossborder_orders_v2.id 删除飞书多维表格中的对应行
    飞书表格中的“订单ID”字段格式为 CB-{id}
    """
    wiki_token = "UD5YweaYsi1AmkkD82Yc6wtBnic"
    table_id = "tblAxLSjtGdLkL9z"          # 跨境物料记录表的真实 ID
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    if not app_token or not access_token:
        return

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}
    target_id = f"CB-{int(order_id)}"

    existing_records = fetch_feishu_all_records(app_token, table_id, headers)
    delete_list = []
    for item in existing_records:
        fields = item.get('fields', {})
        rec_id = str(fields.get('订单ID', '')).strip()
        if rec_id == target_id:
            delete_list.append(item['record_id'])

    if delete_list:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
        try:
            requests.post(url, headers=headers, json={"records": delete_list}, timeout=15)
        except Exception as e:
            safe_log(f"飞书跨境物料订单删除失败: {e}")

if __name__ == "__main__":
    # 1. 迁移采购合同
    migrate_purchase_orders_to_feishu()
    
    # 2. 迁移包装袋发货明细
    migrate_order_history_to_feishu()
    
    # 3. 迁移其他物料发货明细
    migrate_other_material_history_to_feishu()

    # 4. 迁移辅料下单明细
    migrate_accessory_history_to_feishu()

    # 5. 包装袋入库记录表
    migrate_inbound_history_to_feishu()

    # 6. 制衣厂消耗记录表
    migrate_garment_consumption_to_feishu()

    # 7. 跨境物料下单记录表
    migrate_crossborder_orders_to_feishu()