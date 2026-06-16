import os
import requests
import warnings
import time
from database import get_db_conn
from feishu_sync import get_tenant_access_token, get_real_bitable_token
from sync_feishu_to_mysql import sync_feishu_to_mysql

warnings.filterwarnings('ignore')

# 在 sync_all_to_feishu.py 或类似的逻辑文件中添加：
def run_full_sync_flow():
    safe_log("🚀 启动全自动同步闭环：开始执行...")
    
    # 1. 执行从飞书到 MySQL 的同步 (获取基础档案与库存状态)
    safe_log("--- [阶段 1/2] 正在从飞书拉取数据同步至 MySQL ---")
    sync_feishu_to_mysql() 
    
    # 2. 执行从 MySQL 到飞书的推送 (包含你刚才要求的全量工厂镜像对齐)
    safe_log("--- [阶段 2/2] 正在计算并推送到飞书多维表格 ---")
    sync_inventory_mysql_to_feishu()
    
    safe_log("✅ 全闭环同步流程已圆满结束！")

def safe_log(msg):
    try: print(msg.encode('utf-8', errors='ignore').decode('utf-8'))
    except: pass

def fetch_all_feishu_records(app_token, table_id, headers):
    all_items = []
    page_token = ""
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=500&page_token={page_token}"
        res = requests.get(url, headers=headers).json()
        if res.get("code") != 0:
            raise Exception(f"拉取飞书数据失败！表ID: {table_id}, 错误: {res.get('msg')}")
        data = res.get("data", {})
        all_items.extend(data.get("items", []))
        page_token = data.get("page_token", "")
        if not page_token: break
    return all_items

def get_table_fields(app_token, table_id, headers):
    """获取多维表格的所有字段信息，用于动态适配单选/多选类型"""
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    res = requests.get(url, headers=headers).json()
    if res.get("code") != 0:
        raise Exception(f"获取表结构失败: {res.get('msg')}")
    
    field_info = {}
    for item in res.get("data", {}).get("items", []):
        field_info[item["field_name"]] = item["type"]
    return field_info

def parse_fs_val(val):
    if isinstance(val, list):
        if len(val) > 0:
            if isinstance(val[0], dict) and 'text' in val[0]: return str(val[0]['text']).strip()
            return str(val[0]).strip()
        return ""
    return str(val).strip() if val is not None else ""

def clean_invalid_factory_assignments(app_token, table_id, valid_fac_list, headers):
    """【新增】遍历规格表，移除记录中所有不在 MySQL 工厂列表里的工厂"""
    records = fetch_all_feishu_records(app_token, table_id, headers)
    valid_fac_set = set(valid_fac_list)
    
    for item in records:
        rec_id = item['record_id']
        fields = item.get('fields', {})
        current_facs = fields.get('归属工厂', [])
        if not isinstance(current_facs, list): current_facs = [current_facs]
        
        # 核心清洗：只保留在 MySQL 工厂列表里的选项
        cleaned_facs = [f for f in current_facs if f in valid_fac_set]
        
        # 如果长度不一致，说明有失效工厂，必须强制覆盖清理
        if len(cleaned_facs) != len(current_facs):
            payload = {"fields": {"归属工厂": cleaned_facs}}
            url_update = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{rec_id}"
            requests.put(url_update, headers=headers, json=payload)
            safe_log(f"  🧹 已清洗过期工厂: {rec_id}")
            time.sleep(0.05)

def sync_inventory_mysql_to_feishu():
    wiki_token = "GKZIwocjeiHVQfkh3CncvMTvnRc"
    TABLE_SPECS = "tbl4C12TKLWO6DKS"        # 包装袋规格表
    TABLE_INVENTORY = "tblnOrbmw4J2EvFC"      # 库存表明细
    
    def update_feishu_multi_select_options(app_token, table_id, field_name, new_options, headers):
        """通过 Field 定义接口，强制覆盖重写所有下拉菜单选项，彻底删掉已失效的工厂"""
        # 1. 获取所有字段，找到 field_id
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        fields_res = requests.get(url, headers=headers).json()
        field_id = None
        for f in fields_res.get("data", {}).get("items", []):
            if f["field_name"] == field_name:
                field_id = f["field_id"]
                break
        
        if not field_id: return

        # 2. 核心：将 new_options 列表构造成飞书要求的结构，直接覆盖掉旧的所有选项！
        # 只要这个列表里没包含旧的工厂名字，飞书界面上的那个选项就会消失
        payload = {
            "property": {
                "options": [{"name": opt} for opt in new_options]
            }
        }
        
        url_update = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}"
        res = requests.put(url_update, headers=headers, json=payload).json()
        if res.get("code") != 0:
            safe_log(f"⚠️ 选项同步失败: {res.get('msg')}")

    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    if not app_token or not access_token:
        raise Exception("无法获取飞书授权 Token，请检查应用配置。")

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}
    conn = get_db_conn(); cursor = conn.cursor()

    try:
        # ==========================================
        # 动作 1：反向同步【包装袋规格】 (全量镜像对齐 + 自动清洗无效选项)
        # ==========================================
        safe_log("\n[2] 正在反向同步规格的【归属工厂】...")
        
        # 1. 抓取当前所有生效的包装袋工厂 (MySQL 真值)
        cursor.execute("SELECT name FROM packaging_factories WHERE factory_type = '包装袋' OR factory_type IS NULL")
        current_all_facs = [r[0].strip() for r in cursor.fetchall()]
        
        # 2. 【核心修复】：先获取 fs_specs，确保变量已定义且波浪线消失
        fs_specs = fetch_all_feishu_records(app_token, TABLE_SPECS, headers)
        
        # 3. 【新增】：执行全量清洗，把所有规格里不属于 current_all_facs 的过期工厂勾选全部干掉
        safe_log("  🧹 正在执行工厂选项全量清洗...")
        clean_invalid_factory_assignments(app_token, TABLE_SPECS, current_all_facs, headers)
        
        # 4. 执行选项定义更新 (确保多维表格下拉菜单删掉旧工厂)
        update_feishu_multi_select_options(app_token, TABLE_SPECS, "归属工厂", current_all_facs, headers)
        
        # 5. 权限对齐循环
        spec_update_count = 0
        for item in fs_specs:
            rec_id = item['record_id']
            name = parse_fs_val(item['fields'].get('名称', ''))
            size = parse_fs_val(item['fields'].get('尺寸', ''))
            
            cursor.execute("SELECT belong_to FROM bag_specs WHERE name=%s AND size=%s", (name, size))
            res = cursor.fetchone()
            if not res: continue
            
            target_fac_list = [f.strip() for f in res[0].split(',') if f.strip()]
            bl_field = item['fields'].get('归属工厂', [])
            fs_fac_list = [parse_fs_val(f) for f in (bl_field if isinstance(bl_field, list) else [bl_field]) if f]
            
            if set(fs_fac_list) != set(target_fac_list):
                payload = {"fields": {"归属工厂": target_fac_list}}
                url_update = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{TABLE_SPECS}/records/{rec_id}"
                res = requests.put(url_update, headers=headers, json=payload).json()
                if res.get("code") == 0:
                    spec_update_count += 1
                time.sleep(0.05)
                    
        safe_log(f"  ✅ 归属工厂全量镜像对齐完成！共调整了 {spec_update_count} 个规格的工厂授权。")

        # ==========================================
        # 动作 2：反向同步【库存表】 (智能差量覆写防爆版)
        # ==========================================
        safe_log("\n[2] 正在执行库存数据的智能差量写入 (已有的更新，没有的新增)...")
        
        inv_field_info = get_table_fields(app_token, TABLE_INVENTORY, headers)
        
        def get_real_col_name(possible_names):
            for n in possible_names:
                if n in inv_field_info: return n
            return possible_names[0]
            
        col_fac = get_real_col_name(['factory_name', '工厂名称', '发货工厂'])
        col_bag = get_real_col_name(['bag_name', '名称', '包装袋'])
        col_size = get_real_col_name(['bag_size', '尺寸', '包装袋尺寸'])
        col_qty = get_real_col_name(['stock_quantity', '数量', '库存数量'])
        col_price = get_real_col_name(['单价', 'unit_price', 'u_price'])

        fs_inv = fetch_all_feishu_records(app_token, TABLE_INVENTORY, headers)
        fs_map = {}
        for item in fs_inv:
            rec_id = item['record_id']
            fields = item.get('fields', {})
            f_name = parse_fs_val(fields.get(col_fac, ''))
            b_name = parse_fs_val(fields.get(col_bag, ''))
            b_size = parse_fs_val(fields.get(col_size, ''))
            
            # 把飞书上的库存数量也保存下来，用于对比
            raw_qty = fields.get(col_qty, 0)
            try: f_qty = int(float(parse_fs_val(raw_qty) or 0))
            except: f_qty = 0
            
            raw_price = fields.get(col_price, 0)
            try: f_price = float(parse_fs_val(raw_price) or 0)
            except: f_price = 0.0
            
            if not f_name or not b_name: continue

            
            
            key = f"{f_name}_{b_name}_{b_size}".lower()
            fs_map[key] = {
                "rec_id": rec_id,
                "qty": f_qty,
                "price": f_price
            }
            
        # 🌟 SQL加上 unit_price
        cursor.execute("SELECT factory_name, bag_name, bag_size, stock_quantity, unit_price FROM inventory")
        local_inv = cursor.fetchall()
        
        if not local_inv:
            raise Exception("本地 MySQL 库存表完全为空！请先点击左侧的【拉取同步】。")
            
        insert_count = update_count = skip_count = 0
        
        for row in local_inv:
            f_name, b_name, b_size, s_qty, u_price = row
            key = f"{f_name}_{b_name}_{b_size}".lower()
            local_qty = int(s_qty)
            local_price = float(u_price) if u_price is not None else 0.0 # 🌟 本地单价
            
            def format_val(val, col_name):
                if inv_field_info.get(col_name) == 4: return [val] if val else []
                return val

            # 🌟 payload加入单价
            payload = {
                "fields": {
                    col_fac: format_val(f_name, col_fac),
                    col_bag: format_val(b_name, col_bag),
                    col_size: format_val(b_size, col_size),
                    col_qty: local_qty,
                    col_price: local_price 
                }
            }
            
            if key in fs_map:
                fs_data = fs_map.pop(key)
                rec_id = fs_data["rec_id"]
                fs_qty = fs_data["qty"]
                fs_price = fs_data["price"]
                
                # 🌟 判断条件变了：如果数量不一致，或者单价差额超过 0.001，就更新
                if fs_qty != local_qty or abs(fs_price - local_price) > 0.001:
                    url_put = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{TABLE_INVENTORY}/records/{rec_id}"
                    res = requests.put(url_put, headers=headers, json=payload).json()
                    if res.get("code") != 0:
                        raise Exception(f"飞书拒绝更新库存【{f_name} - {b_name}】！错误: {res.get('msg')}")
                    update_count += 1
                else:
                    skip_count += 1
            else:
                # 本地有，飞书没有 -> 新增！
                url_post = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{TABLE_INVENTORY}/records"
                res = requests.post(url_post, headers=headers, json=payload).json()
                if res.get("code") != 0:
                    raise Exception(f"飞书拒绝新增库存行【{f_name} - {b_name}】！错误: {res.get('msg')}")
                insert_count += 1
                
            time.sleep(0.02)
            
        # 清除已被彻底废弃的垃圾组合（如果你不需要系统自动删除过期格子，可以把这段注释掉）
        delete_count = 0
        for remaining_data in fs_map.values():
            url_del = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{TABLE_INVENTORY}/records/{remaining_data['rec_id']}"
            requests.delete(url_del, headers=headers)
            delete_count += 1
            time.sleep(0.02)

        safe_log(f"  🎉 智能推送完成！跳过 {skip_count} 行无变动数据，更新了 {update_count} 行差异数据，新增了 {insert_count} 行，清理了 {delete_count} 行。")

    except Exception as e:
        raise Exception(str(e))
    finally:
        cursor.close()
        conn.close()

def sync_inventory_to_feishu():
    """
    【核心优化】专门供看板快速编辑库存后调用
    全量同步最新库存状态到飞书多维表格：已存在的行更新数量与单价，不存在的行新增，无变动的直接跳过。
    """
    wiki_token = "GKZIwocjeiHVQfkh3CncvMTvnRc" 
    TABLE_INVENTORY = "tblnOrbmw4J2EvFC"      
    
    safe_log("\n[看板库存同步] 正在获取飞书云端凭证...")
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    if not app_token or not access_token:
        return

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}

    inv_field_info = get_table_fields(app_token, TABLE_INVENTORY, headers)
    def get_real_col_name(possible_names):
        for n in possible_names:
            if n in inv_field_info: return n
        return possible_names[0]
        
    col_fac = get_real_col_name(['factory_name', '工厂名称', '发货工厂', '工厂'])
    col_bag = get_real_col_name(['bag_name', '名称', '包装袋', '包装袋名称'])
    col_size = get_real_col_name(['bag_size', '尺寸', '包装袋尺寸'])
    col_qty = get_real_col_name(['stock_quantity', '数量', '库存数量'])
    col_price = get_real_col_name(['单价', 'unit_price', 'u_price'])  # 👈 新增单价列识别

    # 1. 抓取飞书字典
    fs_inv = fetch_all_feishu_records(app_token, TABLE_INVENTORY, headers)
    fs_map = {}
    for item in fs_inv:
        rec_id = item['record_id']
        fields = item.get('fields', {})
        f_name = parse_fs_val(fields.get(col_fac, ''))
        b_name = parse_fs_val(fields.get(col_bag, ''))
        b_size = parse_fs_val(fields.get(col_size, ''))
        
        try: f_qty = int(float(parse_fs_val(fields.get(col_qty, 0)) or 0))
        except: f_qty = 0
        
        # 🌟 抓取飞书现存单价
        try: f_price = float(parse_fs_val(fields.get(col_price, 0)) or 0.0)
        except: f_price = 0.0
        
        if f_name and b_name:
            key = f"{f_name}_{b_name}_{b_size}".lower()
            fs_map[key] = {"rec_id": rec_id, "qty": f_qty, "price": f_price}

    # 2. 从本地 MySQL 读取最新库存与单价
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT factory_name, bag_name, bag_size, stock_quantity, unit_price FROM inventory")
    local_inv = cursor.fetchall()
    cursor.close()
    conn.close()
    
    records_to_update = []
    records_to_create = []
    skip_count = 0
    
    for row in local_inv:
        f_name, b_name, b_size, s_qty, u_price = row
        key = f"{f_name}_{b_name}_{b_size}".lower()
        local_qty = int(s_qty)
        local_price = float(u_price) if u_price is not None else 0.0
        
        def format_val(val, col_name):
            if inv_field_info.get(col_name) == 4: return [val] if val else []
            return val

        # 🌟 构造含有单价的新报文
        payload = {
            "fields": {
                col_fac: format_val(f_name, col_fac),
                col_bag: format_val(b_name, col_bag),
                col_size: format_val(b_size, col_size),
                col_qty: local_qty,
                col_price: local_price
            }
        }
        
        if key in fs_map:
            fs_data = fs_map[key]
            # 🌟 判定：数量或单价任一变动，即触发更新
            if fs_data["qty"] != local_qty or abs(fs_data["price"] - local_price) > 0.001:
                records_to_update.append({"record_id": fs_data["rec_id"], "fields": payload["fields"]})
            else:
                skip_count += 1
        else:
            records_to_create.append({"fields": payload["fields"]})

    # 3. 执行飞书批量更新与新增
    if records_to_update:
        safe_log(f"  👉 正在批量更新飞书 {len(records_to_update)} 条库存明细...")
        for i in range(0, len(records_to_update), 100):
            batch = records_to_update[i:i+100]
            url_update = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{TABLE_INVENTORY}/records/batch_update"
            try: requests.post(url_update, headers=headers, json={"records": batch}, timeout=15)
            except Exception as e: safe_log(f"批量更新失败: {e}")
            time.sleep(0.2)

    if records_to_create:
        safe_log(f"  👉 正在批量新增飞书 {len(records_to_create)} 条新记录...")
        for i in range(0, len(records_to_create), 100):
            batch = records_to_create[i:i+100]
            url_create = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{TABLE_INVENTORY}/records/batch_create"
            try: requests.post(url_create, headers=headers, json={"records": batch}, timeout=15)
            except Exception as e: safe_log(f"批量新增失败: {e}")
            time.sleep(0.2)
            
    safe_log(f"🎉 看板库存与单价更新同步结束！跳过无变动: {skip_count}，更新: {len(records_to_update)}，新增: {len(records_to_create)}。")


def push_new_garment_factory_to_feishu(name, address):
    """
    【新增】将本地新建的制衣厂单向推送到飞书多维表格
    """
    wiki_token = "GKZIwocjeiHVQfkh3CncvMTvnRc"
    table_id = "tbln29kNYwgjGKAj"  # 👈 你指定的制衣厂表格 ID
    
    safe_log(f"\n[实时同步] 正在将新制衣厂【{name}】推送到飞书...")
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    
    if not app_token or not access_token:
        safe_log("❌ 推送制衣厂失败：无法获取飞书 Token")
        return
        
    headers = {
        "Authorization": f"Bearer {access_token}", 
        "Content-Type": "application/json; charset=utf-8"
    }
    
    payload = {
        "fields": {
            "制衣厂名称": name,
            "地址/联系方式": address
        }
    }
    
    url_post = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    try:
        res = requests.post(url_post, headers=headers, json=payload, timeout=10).json()
        if res.get("code") == 0:
            safe_log(f"✅ 成功将新制衣厂推送到飞书！")
        else:
            safe_log(f"⚠️ 推送制衣厂到飞书失败: {res.get('msg')}")
    except Exception as e:
        safe_log(f"💥 推送新制衣厂异常: {e}")

if __name__ == "__main__":
    sync_inventory_mysql_to_feishu()